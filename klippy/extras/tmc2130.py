# TMC2130 configuration
#
# Copyright (C) 2018  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, logging, pins
import tmc2130_extra
import bus

TMC_FREQUENCY = 13200000.
GCONF_EN_PWM_MODE = 1<<2
GCONF_DIAG1_STALL = 1<<8

Registers = tmc2130_extra.Registers

ReadRegisters = [
    "GCONF", "GSTAT", "IOIN", "TSTEP", "XDIRECT", "MSCNT", "MSCURACT",
    "CHOPCONF", "DRV_STATUS", "PWM_SCALE", "LOST_STEPS",
]

class TMC2130:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[1]
        self.spi = bus.MCU_SPI_from_config(config, 3, default_speed=4000000)
        # Allow virtual endstop to be created
        self.diag1_pin = config.get('diag1_pin', None)
        ppins = self.printer.lookup_object("pins")
        ppins.register_chip("tmc2130_" + self.name, self)
        # Add DUMP_TMC command
        gcode = self.printer.lookup_object("gcode")
        step_dist = config.getsection(self.name).getfloat('step_distance')
        gcode.register_mux_command(
            "DUMP_TMC", "STEPPER", self.name,
            self.cmd_DUMP_TMC, desc=self.cmd_DUMP_TMC_help)
        # Get config for initial driver settings
        self.run_current = config.getfloat('run_current', above=0., maxval=2.)
        self.hold_current = config.getfloat(
            'hold_current', self.run_current, above=0., maxval=2.)
        self.homing_current = config.getfloat(
            'homing_current', self.run_current, above=0., maxval=2.)
        self.sense_resistor = config.getfloat('sense_resistor', 0.110, above=0.)
        steps = {'256': 0, '128': 1, '64': 2, '32': 3, '16': 4,
                 '8': 5, '4': 6, '2': 7, '1': 8}
        self.mres = config.getchoice('microsteps', steps)
        self.step_dist_256 = step_dist / (1 << self.mres)

        interpolate = config.getboolean('interpolate', True)
        sc_velocity = config.getfloat('stealthchop_threshold', None, minval=0.)
        sc_threshold = self.velocity_to_clock(sc_velocity)
        self.iholddelay = config.getint(
            'driver_IHOLDDELAY', 8, minval=0, maxval=15)
        tpowerdown = config.getint('driver_TPOWERDOWN', 0, minval=0, maxval=255)
        blank_time_select = config.getint('driver_BLANK_TIME_SELECT', 1,
                                          minval=0, maxval=3)
        toff = config.getint('driver_TOFF', 4, minval=1, maxval=15)
        hend = config.getint('driver_HEND', 7, minval=0, maxval=15)
        hstrt = config.getint('driver_HSTRT', 0, minval=0, maxval=7)
        sgt = config.getint('driver_SGT', 0, minval=-64, maxval=63) & 0x7f
        pwm_scale = config.getboolean('driver_PWM_AUTOSCALE', True)
        pwm_freq = config.getint('driver_PWM_FREQ', 1, minval=0, maxval=3)
        pwm_grad = config.getint('driver_PWM_GRAD', 4, minval=0, maxval=255)
        pwm_ampl = config.getint('driver_PWM_AMPL', 128, minval=0, maxval=255)

        # new CHOPCONF options
        ch_modes = {'SpreadCycle': 0, 'ConstantOff': 1}
        chopper_mode = config.getchoice(
            'chopper_mode', ch_modes, "SpreadCycle")
        rndtf = config.getboolean('driver_RNDTF', False)
        tfd = config.getint('driver_TFD', 0, minval=0, maxval=15)
        offset = config.getint('driver_OFFSET', 0, minval=0, maxval=15)
        disfdcc = config.getboolean('driver_DISFDCC', False)
        vhighfs = config.getboolean('driver_VHIGHFS', False)
        vhighchm = config.getboolean('driver_VHIGHCHM', False)
        sync = config.getint('driver_SYNC', 0, minval=0, maxval=15)
        dedge = config.getboolean('driver_DEDGE', False)
        diss2g = config.getboolean('driver_DISS2G', False)

        # new COOLCONF options
        semin = config.getint('driver_SEMIN', 0, minval=0, maxval=15)
        seup_choices = {'1': 0, '2': 1, '4': 2, '8': 3}
        seup = config.getchoice('driver_SEUP', seup_choices, '1')
        semax = config.getint('driver_SEMAX', 0, minval=0, maxval=15)
        sedn_choices = {'32': 0, '8': 1, '2': 2, '1': 3}
        sedn = config.getchoice('driver_SEDN', sedn_choices, '32')
        seimin = config.getint('driver_SEIMIN', 0, minval=0, maxval=1)
        sfilt = config.getboolean('driver_SFILT', False)

        # other new driver options
        tc_velocity = config.getint(
            'coolstep_threshold', 0, minval=0)
        self.tcoolthrs = self.velocity_to_clock(tc_velocity)
        thigh_velocity = config.getint(
            'thigh_threshold', 0, minval=0)
        th_threshold = self.velocity_to_clock(thigh_velocity)
        self.vsense = config.getboolean('driver_VSENSE', None)

        # Configure registers
        self.reg_GCONF = (sc_velocity is not None) << 2
        if chopper_mode == 0:
            # spreadcycle
            cm_bits = (hstrt << 4) | (hend << 7)
        else:
            # constant off with fast decay
            cm_bits = (((tfd & 0x07) << 4) | (offset << 7) |
                       ((tfd & 0x08) << 11) | (disfdcc << 12))
        self.reg_CHOP_CONF = (toff | cm_bits | (rndtf << 13) |
                              (chopper_mode << 14) |
                              (blank_time_select << 15) | (vhighfs << 18) |
                              (vhighchm << 19) | (sync << 20) |
                              (self.mres << 24) | (interpolate << 28) |
                              (dedge << 29) | (diss2g << 30))
        self.reg_COOL_CONF = (semin | (seup << 5) | (semax << 8) |
                              (sedn << 13) | (seimin << 15) | (sgt << 16) |
                              (sfilt << 24))
        self.reg_PWM_CONF = (pwm_ampl | (pwm_grad << 8) | (pwm_freq << 16) |
                             (pwm_scale << 18))
        self.set_register("GCONF", self.reg_GCONF)
        self.set_current_regs(self.run_current, self.hold_current)
        self.set_register("TPOWERDOWN", tpowerdown)
        self.set_register("TPWMTHRS", max(0, min(0xfffff, sc_threshold)))
        # TODO: need to use velocity to clock for toolthrs and high?
        self.set_register("TCOOLTHRS", max(0, min(0xfffff, self.tcoolthrs)))
        self.set_register("THIGH", max(0, min(0xfffff, th_threshold)))
        self.set_register("COOLCONF", self.reg_COOL_CONF)
        self.set_register("PWMCONF", self.reg_PWM_CONF)
        extras = tmc2130_extra.TMC2130_EXTRA(config, self)
        self.printer_state = extras.printer_state
    def set_current_regs(self, run_current, hold_current):
        def get_bits(rc, hc, vs):
            run = self.current_bits(rc, self.sense_resistor, vs)
            hold = self.current_bits(hc, self.sense_resistor, vs)
            return run, hold
        if self.vsense is None:
            # Auto Calculate Vsense
            vsense = False
            irun, ihold = get_bits(run_current, hold_current, vsense)
            if irun < 16 and ihold < 16:
                vsense = True
                irun, ihold = get_bits(run_current, hold_current, vsense)
        else:
            vsense = self.vsense
            irun, ihold = get_bits(run_current, hold_current, self.vsense)
        self.reg_CHOP_CONF &= ~(1 << 17)
        self.reg_CHOP_CONF |= (vsense << 17)
        ihold_irun = ihold | (irun << 8) | (self.iholddelay << 16)
        self.set_register("CHOPCONF", self.reg_CHOP_CONF)
        self.set_register("IHOLD_IRUN", ihold_irun)
    def current_bits(self, current, sense_resistor, vsense_on):
        sense_resistor += 0.020
        vsense = 0.32
        if vsense_on:
            vsense = 0.18
        cs = int(32. * current * sense_resistor * math.sqrt(2.) / vsense
                 - 1. + .5)
        return max(0, min(31, cs))
    def velocity_to_clock(self, velocity):
        if not velocity:
            return 0
        return int(TMC_FREQUENCY * self.step_dist_256 / velocity + .5)
    def setup_pin(self, pin_type, pin_params):
        if pin_type != 'endstop' or pin_params['pin'] != 'virtual_endstop':
            raise pins.error("tmc2130 virtual endstop only useful as endstop")
        if pin_params['invert'] or pin_params['pullup']:
            raise pins.error("Can not pullup/invert tmc2130 virtual endstop")
        return TMC2130VirtualEndstop(self)
    def get_register(self, reg_name):
        reg = Registers[reg_name]
        self.spi.spi_send([reg, 0x00, 0x00, 0x00, 0x00])
        params = self.spi.spi_transfer([reg, 0x00, 0x00, 0x00, 0x00])
        pr = bytearray(params['response'])
        return (pr[1] << 24) | (pr[2] << 16) | (pr[3] << 8) | pr[4]
    def set_register(self, reg_name, val):
        reg = Registers[reg_name]
        data = [(reg | 0x80) & 0xff, (val >> 24) & 0xff, (val >> 16) & 0xff,
                (val >> 8) & 0xff, val & 0xff]
        self.spi.spi_send(data)
    def get_microsteps(self):
        return 256 >> self.mres
    def get_phase(self):
        return (self.get_register("MSCNT") & 0x3ff) >> self.mres
    cmd_DUMP_TMC_help = "Read and display TMC stepper driver registers"
    def cmd_DUMP_TMC(self, params):
        self.printer.lookup_object('toolhead').get_last_move_time()
        gcode = self.printer.lookup_object('gcode')
        logging.info("DUMP_TMC %s", self.name)
        for reg_name in ReadRegisters:
            val = self.get_register(reg_name)
            msg = "%-15s %08x" % (reg_name + ":", val)
            logging.info(msg)
            gcode.respond_info(msg)

# Endstop wrapper that enables tmc2130 "sensorless homing"
class TMC2130VirtualEndstop:
    def __init__(self, tmc2130):
        self.tmc2130 = tmc2130
        if tmc2130.diag1_pin is None:
            raise pins.error("tmc2130 virtual endstop requires diag1_pin")
        ppins = tmc2130.printer.lookup_object('pins')
        self.mcu_endstop = ppins.setup_pin('endstop', tmc2130.diag1_pin)
        if self.mcu_endstop.get_mcu() is not tmc2130.spi.get_mcu():
            raise pins.error("tmc2130 virtual endstop must be on same mcu")
        # Wrappers
        self.get_mcu = self.mcu_endstop.get_mcu
        self.add_stepper = self.mcu_endstop.add_stepper
        self.get_steppers = self.mcu_endstop.get_steppers
        self.home_start = self.mcu_endstop.home_start
        self.home_wait = self.mcu_endstop.home_wait
        self.query_endstop = self.mcu_endstop.query_endstop
        self.query_endstop_wait = self.mcu_endstop.query_endstop_wait
        self.TimeoutError = self.mcu_endstop.TimeoutError
    def home_prepare(self):
        gconf = self.tmc2130.reg_GCONF
        gconf &= ~GCONF_EN_PWM_MODE
        gconf |= GCONF_DIAG1_STALL
        self.tmc2130.set_register("GCONF", gconf)
        self.tmc2130.set_current_regs(
            self.tmc2130.homing_current, self.tmc2130.hold_current)
        self.tmc2130.set_register("TCOOLTHRS", 0xfffff)
        self.mcu_endstop.home_prepare()
    def home_finalize(self):
        self.tmc2130.set_register("GCONF", self.tmc2130.reg_GCONF)
        self.tmc2130.set_current_regs(
            self.tmc2130.run_current, self.tmc2130.hold_current)
        self.tmc2130.set_register(
            "TCOOLTHRS", max(0, min(0xfffff, self.tmc2130.tcoolthrs)))
        self.mcu_endstop.home_finalize()

def load_config_prefix(config):
    return TMC2130(config)
