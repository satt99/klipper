# Fan Tachometer
#
# Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

TACH_UPDATE_TIME = 2.0
MAX_TACH_ERRORS = 4

# Sample pulses on falling edge
PIN_IRQ_MODE = 2

FAN_TYPES = {"3wire": 0, "4wire": 1}

def do_shutdown(printer, msg):
    gcode = printer.lookup_object('gcode')
    gcode.respond_info(msg)
    logging.info(msg)
    printer.invoke_shutdown(msg)

class Tachometer:
    TIMER_INIT = False
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.name = config.get_name().split()[-1]
        self.gcode = self.printer.lookup_object('gcode')
        ppins = self.printer.lookup_object('pins')
        pin = config.get('tach_pin')
        pin_params = ppins.lookup_pin(pin)
        self.mcu = pin_params['chip']
        self.pin = pin_params['pin']
        self.oid = self.mcu.create_oid()

        self.fan_type = config.getchoice('fan_type', FAN_TYPES, '3wire')
        self.pulses_per_rev = config.getint(
            'pulses_per_revolution', 2, minval=1)
        self.min_fan_speed = config.getfloat(
            'min_fan_speed', None, above=0.)
        self.last_pwm = 0.
        self.last_rpm = 0.
        self.fan_errors = 0
        self.update_timer_cmd = self.set_state_cmd = None
        self.enabled = False

        self.mcu.register_config_callback(self._build_config)
        self.printer.register_event_handler(
            "klippy:ready", self._handle_ready)
        self.printer.register_event_handler(
            "gcode:request_restart", self._handle_restart)
        self.gcode.register_mux_command(
            "QUERY_FAN_SPEED", "FAN", self.name,
            self.cmd_QUERY_FAN_SPEED,
            desc=self.cmd_QUERY_FAN_SPEED_help)
    def _build_config(self):
        self.mcu.add_config_cmd(
            "config_tachometer oid=%d pin=%s" % (self.oid, self.pin))
        cmd_queue = self.mcu.alloc_command_queue()
        self.update_timer_cmd = self.mcu.lookup_command(
            "update_tach_timer clock=%u rest_ticks=%u", cq=cmd_queue)
        # XXX - register toggle tach command
        self.set_state_cmd = self.mcu.lookup_command(
            "set_tach_irq_state oid=%c mode=%c", cq=cmd_queue)
        self.mcu.register_msg(
            self._handle_tach_response, "tach_response", self.oid)
    def _handle_ready(self):
        if not self.TIMER_INIT:
            self.TIMER_INIT = True
            clock = self.mcu.get_query_slot(self.oid)
            rest_ticks = self.mcu.seconds_to_clock(TACH_UPDATE_TIME)
            self.update_timer_cmd.send([clock, rest_ticks])
        if self.fan_type == FAN_TYPES['4wire']:
            self.enable()
    def _handle_restart(self, print_time):
        if self.TIMER_INIT:
            self.TIMER_INIT = False
            self.update_timer_cmd.send([0, 0])
        self.disable()
    def _handle_tach_response(self, params):
        pulses = params['pulse_count']
        rpm = (pulses / float(self.pulses_per_rev) / TACH_UPDATE_TIME) * 60.
        self.reactor.register_async_callback(
            (lambda e, s=self, r=rpm: s.check_speed(e, r)))
    def check_speed(self, eventtime, rpm):
        if self.enabled:
            diff = abs(rpm - self.last_rpm)
            if diff >= (self.last_rpm * .2):
                # 20% or more change, log it
                logging.debug(
                    "%s: Fan Speed change, last: %d current: %d "
                    "difference: %d" % (self.name, self.last_rpm, rpm, diff))
            self.last_rpm = rpm
            if self.min_fan_speed is not None and self.last_pwm > 0.:
                if rpm < self.min_fan_speed:
                    self.fan_errors += 1
                else:
                    self.fan_errors = 0
                if self.fan_errors >= MAX_TACH_ERRORS:
                    do_shutdown(
                        self.printer,
                        "[%s]: Fan speed below minimum threshold!"
                        % (self.name))
    def disable(self):
        if self.enabled:
            self.enabled = False
            self.set_state_cmd.send([self.oid, 4])
    def enable(self):
        if not self.enabled:
            self.enabled = True
            self.set_state_cmd.send([self.oid, PIN_IRQ_MODE])
    def update(self, value):
        self.last_pwm = value
        if self.fan_type == FAN_TYPES['3wire']:
            if value == 1.:
                self.enable()
            else:
                self.disable()
    cmd_QUERY_FAN_SPEED_help = "Return the fan's reported speed"
    def cmd_QUERY_FAN_SPEED(self, params):
        if self.enabled:
            msg = "%s: Current fan speed = %d rpm" % (
                self.name, self.last_rpm)
        else:
            msg = "%s: Tachometer disabled" % (self.name)
        self.gcode.respond_info(msg)

class LockedRotorSensor:
    def __init(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.name = config.get_name().split()[-1]
        buttons = self.printer.try_load_module(config, 'buttons')
        rotor_pin = config.get('locked_rotor_pin')
        buttons.register_buttons([rotor_pin], self._handle_rotor_state)
        self.recovery_time = config.getfloat(
            'locked_recovery_time', 5., minval=1.)
        self.last_rotor_state = True
        self.rotor_locked = False
        self.enabled = False
    def _handle_rotor_state(self, eventtime, state):
        self.last_rotor_state = state
        if not self.enabled:
            return
        if not state and not self.rotor_locked:
            self.rotor_locked = True
            waketime = self.reactor.monotonic() + self.recovery_time
            self.reactor.register_callback(self._locked_rotor_event, waketime)
    def _locked_rotor_event(self, eventtime):
        if not self.last_rotor_state and self.enabled:
            # rotor locked, shutdown
            do_shutdown(
                self.printer, "[%s]: Locked Rotor detected!" % (self.name))
        self.rotor_locked = False
    def update(self, value):
        self.enabled = (value == 1.)

class NoSensor:
    def update(self, value):
        pass

def get_fan_sensor(config):
    if config.get('tach_pin', None) is not None:
        return Tachometer(config)
    elif config.get('locked_rotor_pin', None) is not None:
        return LockedRotorSensor(config)
    else:
        return NoSensor()
