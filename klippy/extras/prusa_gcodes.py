# Extended Gcode Commands
#
# Copyright (C) 2018  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import homing
import thermistor
import threading

TMC_REG_COOLCONF = 0x6d

class GCodeTimer:
    def __init__(self, reactor, gcode, script, delay):
        self.reactor = reactor
        self.gcode = gcode
        self.script = script
        delay_time = reactor.monotonic() + delay
        self.timer = reactor.register_timer(self.invoke, delay_time)
    def invoke(self, eventtime):
        self.reactor.unregister_timer(self.timer)
        self.gcode.run_script_from_command(self.script)
        return self.reactor.NEVER

class PrusaGcodes:
    def __init__(self, config):
        self.supported_gcodes = {
            'LOAD_FILAMENT': False,
            'UNLOAD_FILAMENT': False,
            'M900': False,
            'GET_AMBIENT_TEMP': False,
            'TIMED_GCODE': True,
            'TRAM_Z': False}
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        mcu = self.printer.lookup_object('mcu')
        mcu.register_config_callback(self.build_config)
        self.tram_current = config.getfloat(
            'tram_current', None)
        self.supported_gcodes['M900'] = config.getboolean(
            'enable_M900', False)
        self.ambient_sensor = None
        self.temp_lock = None
        self.ambient_temp = 0.
        if config.has_section('thermistor prusa_ambient'):
            atemp_cfg = config.getsection('thermistor prusa_ambient')
            sensor = thermistor.CustomThermistor(atemp_cfg)
            self.ambient_sensor = sensor.create(config)
            self.ambient_sensor.setup_minmax(0., 85.)
            self.ambient_sensor.setup_callback(self.temperature_callback)
            self.supported_gcodes['GET_AMBIENT_TEMP'] = True
            self.temp_lock = threading.Lock()
        # TODO: the correct way to do this is to get the pressure
        # for each extruder if there are multiples
        self.default_pressure = 0.
        if config.has_section('extruder'):
            e_config = config.getsection('extruder')
            self.default_pressure = e_config.getfloat(
                'pressure_advance', 0., minval=0.)
        self.extruder_type = config.get(
            "extruder_type", None)
        if self.extruder_type is not None:
            if self.extruder_type in ["prusa", "skelestruder"]:
                self.extruder_type = self.extruder_type.strip().lower()
                self.supported_gcodes['LOAD_FILAMENT'] = True
                self.supported_gcodes['UNLOAD_FILAMENT'] = True
            else:
                raise config.error(
                    "custom_gcodes: unknow extruder type <%s>"
                    % self.extruder_type)
        self.tmc_z_endstop = None
        self.z_rail = None
        self._setup_virtual_z_endstop(config)
        self.display = None
        self.gcode = self.printer.lookup_object('gcode')
        for key in self.supported_gcodes:
            if self.supported_gcodes[key]:
                try:
                    command_func = getattr(self, "cmd_" + key)
                    help_attr = getattr(self, "cmd_" + key + "_help")
                except:
                    raise config.error("Gcode [%s] Not supported" % (key))
                self.gcode.register_command(key, command_func,
                                            desc=help_attr)
                logging.info("Extended gcode " + key + " enabled")
        self.printer.register_event_handler("klippy:ready",
                                            self.handle_ready)
    def handle_ready(self):
        try:
            self.display = self.printer.lookup_object('display')
        except:
            self.display = None
            self.gcode.respond_info("Display not added to config")
    def build_config(self):
        if self.supported_gcodes['TRAM_Z'] and self.tmc_z_endstop:
            kin = self.printer.lookup_object('toolhead').get_kinematics()
            self.z_rail = kin.rails[2]
            if self.z_rail.name != 'z':
                self.z_rail = None
            else:
                for stepper in self.z_rail.get_steppers():
                    stepper.add_to_endstop(self.tmc_z_endstop[0])
    def _setup_virtual_z_endstop(self, config):
        if config.has_section('stepper_z'):
            z_config = config.getsection('stepper_z')
            endstop = z_config.get('endstop_pin')
            if endstop.startswith("tmc2130"):
                # already_using virtual endstop, just issue a
                # regular homing move
                self.supported_gcodes['TRAM_Z'] = True
            elif config.has_section('tmc2130 stepper_z'):
                ppins = self.printer.lookup_object('pins')
                es = ppins.setup_pin(
                    'endstop', 'tmc2130_stepper_z:virtual_endstop')
                self.tmc_z_endstop = (es, "tmc2130_z_endstop")
                self.supported_gcodes['TRAM_Z'] = True
    def temperature_callback(self, readtime, temp):
        with self.temp_lock:
            self.ambient_temp = temp
    cmd_LOAD_FILAMENT_help = "Load filament into Extruder"
    def cmd_LOAD_FILAMENT(self, params):
        # TODO: use buttons to ask if load should continue
        self.gcode.respond_info("Load Filament for extruder: %s"
                                % self.extruder_type)
        if self.extruder_type == "skelestruder":
            def_len = 85.
        else:
            def_len = 75.
        length = self.gcode.get_float('LENGTH', params, def_len, above=25.)
        length -= 25.
        first_extrude = "G1 E%.2f F400" % (length)
        toolhead = self.printer.lookup_object('toolhead')
        if self.display:
            self.display.set_message("Loading Filament...")
        self.gcode.run_script_from_command("M83")
        self.gcode.run_script_from_command("G92 E0.0")
        self.gcode.run_script_from_command(first_extrude)
        self.gcode.run_script_from_command("G1 E25 F100")
        self.gcode.run_script_from_command("G92 E0.0")
        toolhead.wait_moves()
        if self.display:
            self.display.set_message("Load Complete", 5.)
    cmd_UNLOAD_FILAMENT_help = "Unload filament from Extruder"
    def cmd_UNLOAD_FILAMENT(self, params):
        self.gcode.respond_info("Load Filament for extruder: %s"
                                % self.extruder_type)
        toolhead = self.printer.lookup_object('toolhead')
        length = self.gcode.get_float('LENGTH', params, 80., minval=42.)
        if self.extruder_type == "skelestruder":
            if length - 42. > 20.:
                last_extrude = "G1 E%.2f F1000" % (-1 * (length - 42.))
            else:
                last_extrude = "G1 E-20 F1000"
            if self.display:
                self.display.set_message("Unloading Filament...")
            self.gcode.run_script_from_command("M83")
            self.gcode.run_script_from_command("G92 E0.0")
            self.gcode.run_script_from_command("G1 E-32 F5200")
            self.gcode.run_script_from_command("G1 E-10 F100")
            self.gcode.run_script_from_command(last_extrude)
        else:
            if length - 60. > 20.:
                last_extrude = "G1 E%.2f F1000" % (-1 * (length - 42.))
            else:
                last_extrude = "G1 E-20 F1000"
            if self.display:
                self.display.set_message("Unloading Filament...")
            self.gcode.run_script_from_command("M83")
            self.gcode.run_script_from_command("G92 E0.0")
            self.gcode.run_script_from_command("G1 E-45 F5200")
            self.gcode.run_script_from_command("G1 E-15 F1000")
            self.gcode.run_script_from_command(last_extrude)
        self.gcode.run_script_from_command("G92 E0.0")
        toolhead.wait_moves()
        toolhead.motor_off()
        if self.display:
            self.display.set_message("REMOVE FILAMENT NOW!", 5.)
        self.gcode.run_script_from_command("SET_BEEPER DURATION=1")
    cmd_LOAD_CONTINUE_help = "Extends filament load to extrude more"
    def cmd_LOAD_CONTINUE(self, params):
        length = self.gcode.get_float('LENGTH', params, 50., above=25.)
        toolhead = self.printer.lookup_object('toolhead')
        e_code = "G1 E%.2f F100" % (length)
        self.gcode.run_script_from_command(e_code)
        toolhead.wait_moves()
    cmd_TRAM_Z_help = "Rams Z-axis to top z-holders, used to align Axis"
    def cmd_TRAM_Z(self, params):
        if self.z_rail is None:
            self.gcode.respond_info("Unable to locate z-rail, aborting")
            return
        toolhead = self.printer.lookup_object('toolhead')
        z_tmc2130 = self.printer.lookup_object('tmc2130 stepper_z')
        event_time = self.reactor.monotonic()
        status = toolhead.get_status(event_time)
        if status['status'] == "Printing":
            self.gcode.respond_info("Cannot Tram during a print, aborting")
            return
        if self.tmc_z_endstop is None:
            # TODO: Already Using TMC to home.  Just homing max, home
            # to top, move up 10mm?
            return
        homing_max = self.z_rail.position_max
        speed = self.z_rail.homing_speed
        self.z_rail.position_max = 230.
        cur_pos = toolhead.get_position()
        start_pos = list(cur_pos)
        move_pos = list(cur_pos)
        start_pos[2] = self.z_rail.position_min
        move_pos[2] = 220.0
        toolhead.set_position(start_pos, homing_axes=[2])
        move_d = abs(220.0 - self.z_rail.position_min)
        est_steps = sum([move_d / s.get_step_dist()
                        for s in self.z_rail.get_steppers()])
        dwell_t = est_steps * homing.HOMING_STEP_DELAY
        home = homing.Homing(self.printer)
        try:
            home.homing_move(move_pos, [self.tmc_z_endstop], speed,
                             dwell_t=dwell_t)
        except homing.EndstopError as e:
            reason = str(e)
            raise self.gcode.error(reason)
        toolhead.wait_moves()
        if self.tram_current is not None:
            z_tmc2130.set_current_regs(
                self.tram_current, z_tmc2130.hold_current)
        # Move up 10mm for tramming
        next_pos = toolhead.get_position()
        next_pos[2] += 10
        toolhead.move(next_pos, 10.)
        next_pos[2] -= 50
        toolhead.move(next_pos, 10.)
        toolhead.wait_moves()
        toolhead.motor_off()
        if self.tram_current is not None:
            z_tmc2130.set_current_regs(
                z_tmc2130.run_current, z_tmc2130.hold_current)
        self.z_rail.position_max = homing_max
    cmd_M900_help = "Enable/Disable pressure advance"
    def cmd_M900(self, params):
        if 'K' in params:
            pressure = self.gcode.get_int('K', params)
            pa_gcode = "SET_PRESSURE_ADVANCE ADVANCE=%.4f"
            if pressure:
                self.gcode.run_script_from_command(
                    pa_gcode % self.default_pressure)
            else:
                self.gcode.run_script_from_command(pa_gcode % 0.0)
    cmd_TIMED_GCODE_help = "Run GCODE script from a timer"
    def cmd_TIMED_GCODE(self, params):
        script = self.gcode.get_str('GCODE', params)
        delay_time = self.gcode.get_int('DELAY', params, 0, minval=0)
        # TODO: Instead of a simple underscore, replace with ability
        # to use escape chars (possibly regex)
        script = script.replace('_', " ")
        GCodeTimer(self.reactor, self.gcode, script, delay_time)
    cmd_GET_AMBIENT_TEMP_help = "Read abmient temperature"
    def cmd_GET_AMBIENT_TEMP(self, params):
        with self.temp_lock:
            self.gcode.respond_info(
                "Current Ambient Temperature: %.2fC" % self.ambient_temp)
def load_config(config):
    return PrusaGcodes(config)
