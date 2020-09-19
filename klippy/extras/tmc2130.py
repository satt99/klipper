# TMC2130 configuration
#
# Copyright (C) 2018-2019  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, logging
from . import bus, tmc

TMC_FREQUENCY=13200000.

Registers = {
    "GCONF": 0x00, "GSTAT": 0x01, "IOIN": 0x04, "IHOLD_IRUN": 0x10,
    "TPOWERDOWN": 0x11, "TSTEP": 0x12, "TPWMTHRS": 0x13, "TCOOLTHRS": 0x14,
    "THIGH": 0x15, "XDIRECT": 0x2d, "MSLUT0": 0x60, "MSLUT1": 0x61,
    "MSLUT2": 0x62, "MSLUT3": 0x63, "MSLUT4": 0x64, "MSLUT5": 0x65,
    "MSLUT6": 0x66, "MSLUT7": 0x67, "MSLUTSEL": 0x68, "MSLUTSTART": 0x69,
    "MSCNT": 0x6a, "MSCURACT": 0x6b, "CHOPCONF": 0x6c, "COOLCONF": 0x6d,
    "DCCTRL": 0x6e, "DRV_STATUS": 0x6f, "PWMCONF": 0x70, "PWM_SCALE": 0x71,
    "ENCM_CTRL": 0x72, "LOST_STEPS": 0x73,
}

ReadRegisters = [
    "GCONF", "GSTAT", "IOIN", "TSTEP", "XDIRECT", "MSCNT", "MSCURACT",
    "CHOPCONF", "DRV_STATUS", "PWM_SCALE", "LOST_STEPS",
]

Fields = {}
Fields["GCONF"] = {
    "I_scale_analog": 1<<0, "internal_Rsense": 1<<1, "en_pwm_mode": 1<<2,
    "enc_commutation": 1<<3, "shaft": 1<<4, "diag0_error": 1<<5,
    "diag0_otpw": 1<<6, "diag0_stall": 1<<7, "diag1_stall": 1<<8,
    "diag1_index": 1<<9, "diag1_onstate": 1<<10, "diag1_steps_skipped": 1<<11,
    "diag0_int_pushpull": 1<<12, "diag1_pushpull": 1<<13,
    "small_hysteresis": 1<<14, "stop_enable": 1<<15, "direct_mode": 1<<16,
    "test_mode": 1<<17
}
Fields["GSTAT"] = { "reset": 1<<0, "drv_err": 1<<1, "uv_cp": 1<<2 }
Fields["IOIN"] = {
    "STEP": 1<<0, "DIR": 1<<1, "DCEN_CFG4": 1<<2, "DCIN_CFG5": 1<<3,
    "DRV_ENN_CFG6": 1<<4, "DCO": 1<<5, "VERSION": 0xff << 24
}
Fields["IHOLD_IRUN"] = {
    "IHOLD": 0x1f << 0, "IRUN": 0x1f << 8, "IHOLDDELAY": 0x0f << 16
}
Fields["TPOWERDOWN"] = { "TPOWERDOWN": 0xff }
Fields["TSTEP"] = { "TSTEP": 0xfffff }
Fields["TPWMTHRS"] = { "TPWMTHRS": 0xfffff }
Fields["TCOOLTHRS"] = { "TCOOLTHRS": 0xfffff }
Fields["THIGH"] = { "THIGH": 0xfffff }
Fields["MSCNT"] = { "MSCNT": 0x3ff }
Fields["MSCURACT"] = { "CUR_A": 0x1ff, "CUR_B": 0x1ff << 16 }
Fields["CHOPCONF"] = {
    "toff": 0x0f, "hstrt": 0x07 << 4, "hend": 0x0f << 7, "fd3": 1<<11,
    "disfdcc": 1<<12, "rndtf": 1<<13, "chm": 1<<14, "TBL": 0x03 << 15,
    "vsense": 1<<17, "vhighfs": 1<<18, "vhighchm": 1<<19, "sync": 0x0f << 20,
    "MRES": 0x0f << 24, "intpol": 1<<28, "dedge": 1<<29, "diss2g": 1<<30
}
Fields["COOLCONF"] = {
    "semin": 0x0f, "seup": 0x03 << 5, "semax": 0x0f << 8, "sedn": 0x03 << 13,
    "seimin": 1<<15, "sgt": 0x7f << 16, "sfilt": 1<<24
}
Fields["DRV_STATUS"] = {
    "SG_RESULT": 0x3ff, "fsactive": 1<<15, "CS_ACTUAL": 0x1f << 16,
    "stallGuard": 1<<24, "ot": 1<<25, "otpw": 1<<26, "s2ga": 1<<27,
    "s2gb": 1<<28, "ola": 1<<29, "olb": 1<<30, "stst": 1<<31
}
Fields["PWMCONF"] = {
    "PWM_AMPL": 0xff, "PWM_GRAD": 0xff << 8, "pwm_freq": 0x03 << 16,
    "pwm_autoscale": 1<<18, "pwm_symmetric": 1<<19, "freewheel": 0x03 << 20
}
Fields["PWM_SCALE"] = { "PWM_SCALE": 0xff }
Fields["LOST_STEPS"] = { "LOST_STEPS": 0xfffff }

Fields["MSLUTSTART"] = { "MSLUTSTART": 0xff00ff}
Fields["MSLUTSEL"] = { "MSLUTSEL": 0xffffffff }
for i in range(8):
    name = "MSLUT" + str(i)
    Fields[name] = { name: 0xffffffff }

SignedFields = ["CUR_A", "CUR_B", "sgt"]

FieldFormatters = {
    "I_scale_analog":   (lambda v: "1(ExtVREF)" if v else ""),
    "shaft":            (lambda v: "1(Reverse)" if v else ""),
    "drv_err":          (lambda v: "1(ErrorShutdown!)" if v else ""),
    "uv_cp":            (lambda v: "1(Undervoltage!)" if v else ""),
    "VERSION":          (lambda v: "%#x" % v),
    "MRES":             (lambda v: "%d(%dusteps)" % (v, 0x100 >> v)),
    "otpw":             (lambda v: "1(OvertempWarning!)" if v else ""),
    "ot":               (lambda v: "1(OvertempError!)" if v else ""),
    "s2ga":             (lambda v: "1(ShortToGND_A!)" if v else ""),
    "s2gb":             (lambda v: "1(ShortToGND_B!)" if v else ""),
    "ola":              (lambda v: "1(OpenLoad_A!)" if v else ""),
    "olb":              (lambda v: "1(OpenLoad_B!)" if v else ""),
}


######################################################################
# TMC stepper current config helper
######################################################################

MAX_CURRENT = 2.000

class TMCCurrentHelper:
    def __init__(self, config, mcu_tmc):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        self.mcu_tmc = mcu_tmc
        self.fields = mcu_tmc.get_fields()
        run_current = config.getfloat('run_current',
                                      above=0., maxval=MAX_CURRENT)
        hold_current = config.getfloat('hold_current', run_current,
                                       above=0., maxval=MAX_CURRENT)
        self.homing_current = config.getfloat('homing_current', run_current,
                                              above=0., maxval=MAX_CURRENT)
        self.sense_resistor = config.getfloat('sense_resistor', 0.110, above=0.)
        vsense, irun, ihold = self._calc_current(run_current, hold_current)
        self.fields.set_field("vsense", vsense)
        self.fields.set_field("IHOLD", ihold)
        self.fields.set_field("IRUN", irun)
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command("SET_TMC_CURRENT", "STEPPER", self.name,
                                   self.cmd_SET_TMC_CURRENT,
                                   desc=self.cmd_SET_TMC_CURRENT_help)
    def _calc_current_bits(self, current, vsense):
        sense_resistor = self.sense_resistor + 0.020
        vref = 0.32
        if vsense:
            vref = 0.18
        cs = int(32. * current * sense_resistor * math.sqrt(2.) / vref
                 - 1. + .5)
        return max(0, min(31, cs))
    def _calc_current(self, run_current, hold_current):
        vsense = False
        irun = self._calc_current_bits(run_current, vsense)
        ihold = self._calc_current_bits(min(hold_current, run_current),
                                        vsense)
        if irun < 16 and ihold < 16:
            vsense = True
            irun = self._calc_current_bits(run_current, vsense)
            ihold = self._calc_current_bits(min(hold_current, run_current),
                                            vsense)
        return vsense, irun, ihold
    def _calc_current_from_field(self, field_name):
        bits = self.fields.get_field(field_name)
        sense_resistor = self.sense_resistor + 0.020
        vref = 0.32
        if self.fields.get_field("vsense"):
            vref = 0.18
        current = (bits + 1) * vref / (32 * sense_resistor * math.sqrt(2.))
        return round(current, 2)
    def get_current(self):
        run_current = self._calc_current_from_field("IRUN")
        hold_current = self._calc_current_from_field("IHOLD")
        return run_current, hold_current, self.homing_current
    def set_current(self, run_current, hold_current):
        print_time = self.printer.lookup_object('toolhead').get_last_move_time()
        vsense, irun, ihold = self._calc_current(run_current, hold_current)
        if vsense != self.fields.get_field("vsense"):
            val = self.fields.set_field("vsense", vsense)
            self.mcu_tmc.set_register("CHOPCONF", val, print_time)
        self.fields.set_field("IHOLD", ihold)
        val = self.fields.set_field("IRUN", irun)
        self.mcu_tmc.set_register("IHOLD_IRUN", val, print_time)
    cmd_SET_TMC_CURRENT_help = "Set the current of a TMC driver"
    def cmd_SET_TMC_CURRENT(self, gcmd):
        run_current = gcmd.get_float('CURRENT', None,
                                     minval=0., maxval=MAX_CURRENT)
        hold_current = gcmd.get_float('HOLDCURRENT', None,
                                      above=0., maxval=MAX_CURRENT)
        if run_current is None and hold_current is None:
            # Query only
            run_current = self._calc_current_from_field("IRUN")
            hold_current = self._calc_current_from_field("IHOLD")
            gcmd.respond_info("Run Current: %0.2fA Hold Current: %0.2fA"
                              % (run_current, hold_current))
            return
        if run_current is None:
            run_current = self._calc_current_from_field("IRUN")
        if hold_current is None:
            hold_current = self._calc_current_from_field("IHOLD")
        self.set_current(run_current, hold_current)


######################################################################
# TMC2130 SPI
######################################################################

# Helper code for working with TMC devices via SPI
class MCU_TMC_SPI:
    def __init__(self, config, name_to_reg, fields):
        self.printer = config.get_printer()
        self.mutex = self.printer.get_reactor().mutex()
        self.spi = bus.MCU_SPI_from_config(config, 3, default_speed=4000000)
        self.name_to_reg = name_to_reg
        self.fields = fields
    def get_fields(self):
        return self.fields
    def get_register(self, reg_name):
        reg = self.name_to_reg[reg_name]
        with self.mutex:
            self.spi.spi_send([reg, 0x00, 0x00, 0x00, 0x00])
            if self.printer.get_start_args().get('debugoutput') is not None:
                return 0
            params = self.spi.spi_transfer([reg, 0x00, 0x00, 0x00, 0x00])
        pr = bytearray(params['response'])
        return (pr[1] << 24) | (pr[2] << 16) | (pr[3] << 8) | pr[4]
    def set_register(self, reg_name, val, print_time=None):
        minclock = 0
        if print_time is not None:
            minclock = self.spi.get_mcu().print_time_to_clock(print_time)
        reg = self.name_to_reg[reg_name]
        data = [(reg | 0x80) & 0xff, (val >> 24) & 0xff, (val >> 16) & 0xff,
                (val >> 8) & 0xff, val & 0xff]
        with self.mutex:
            self.spi.spi_send(data, minclock)

######################################################################
# TMC2130 extras
######################################################################
TMC_WAVE_FACTOR_MIN = 1.005
TMC_WAVE_FACTOR_MAX = 1.2
TMC_WAVE_AMP = 247

class TMC2130_EXTRA:
    GCODES = [
        "SET_WAVE", "SET_STEP", "SET_STEALTH", "SET_PWMCONF"]
    WAVE_REGS = [
        "MSLUTSTART", "MSLUTSEL", "MSLUT0", "MSLUT1", "MSLUT2",
        "MSLUT3", "MSLUT4", "MSLUT5", "MSLUT6", "MSLUT7"]
    def __init__(self, config, tmc2130):
        self.printer = config.get_printer()
        self._stepper = None
        self.tmc2130 = tmc2130
        self.name = config.get_name().split()[-1]
        self.fields = tmc2130.fields
        self.regs = self.fields.registers
        self.get_microsteps = tmc2130.get_microsteps
        self.get_phase = tmc2130.get_phase
        self.set_register = tmc2130.mcu_tmc.set_register
        self.get_register = tmc2130.mcu_tmc.get_register

        stepper_config = config.getsection(self.name)
        step_dist = stepper_config.getfloat('step_distance')
        self.step_dist_256 = step_dist / (1 << self.fields.get_field("MRES"))

        dir_pin = config.getsection(self.name).get('dir_pin')
        self.inverted = dir_pin.startswith("!")

        tc_vel = config.getfloat('coolstep_threshold', 0., minval=0.)
        tc_thrs = self.velocity_to_thrs(tc_vel)
        self.fields.set_field("TCOOLTHRS", tc_thrs)
        thigh_vel = config.getfloat('thigh_threshold', 0., minval=0.)
        th_thrs = self.velocity_to_thrs(thigh_vel)
        self.fields.set_field('THIGH', th_thrs)

        gcode = self.printer.lookup_object("gcode")
        for gc in self.GCODES:
            tmc_gc = "TMC_" + gc
            try:
                command_func = getattr(self, "cmd_" + tmc_gc)
                help_attr = getattr(self, "cmd_" + tmc_gc + "_help")
            except:
                raise config.error("TMC Gcode [%s] Not supported" % (tmc_gc))
            gcode.register_mux_command(
                tmc_gc, "STEPPER", self.name, command_func, desc=help_attr)
        wave_factor = config.getfloat('linearity_correction', None,
                                      minval=0., maxval=1.2)
        if wave_factor is not None:
            self._set_wave(wave_factor, is_init=True)
        self.printer.register_event_handler(
            "klippy:ready", self.handle_ready)
    def handle_ready(self):
        # TODO: It might be better to look up the correct stepper
        # in the TMC_SET_STEP gcode rather than store it initially
        toolhead = self.printer.lookup_object('toolhead')
        if self.name == 'extruder':
            self._stepper = toolhead.get_extruder().stepper
            logging.info("TMC2130 %s: Stepper Found" % (self.name))
        else:
            steppers = toolhead.get_kinematics().get_steppers()
            for s in steppers:
                if s.get_name() == self.name:
                    self._stepper = s
                    logging.info("TMC2130 %s: Stepper Found" % (self.name))
                    break
            if self._stepper is None:
                logging.info("TMC2130 %s: Stepper NOT Found" % (self.name))
    def velocity_to_thrs(self, velocity):
        if not velocity:
            return 0
        thrs = int(TMC_FREQUENCY * self.step_dist_256 / velocity + .5)
        return max(0, min(0xfffff, thrs))
    def _set_default_wave(self, is_init=False):
        # default wave regs from page 79 of TMC2130 datasheet
        msg = "TMC2130: Wave factor on stepper [%s] set to default" \
            % (self.name)
        default_vals = [
            0x00F70000, 0xFFFF8056, 0xAAAAB554, 0x4A9554AA, 0x24492929,
            0x10104222, 0xFBFFFFFF, 0xB5BB777D, 0x49295556, 0x00404222
        ]
        for reg, val in zip(self.WAVE_REGS, default_vals):
            self.fields.set_field(reg, val)
            if not is_init:
                self.set_register(reg, val)
        logging.info(msg)
    def _set_wave(self, fac, use_default_wave=False, is_init=False):
        if fac < TMC_WAVE_FACTOR_MIN:
            fac = 0.0
        elif fac > TMC_WAVE_FACTOR_MAX:
            fac = TMC_WAVE_FACTOR_MAX
        if use_default_wave and fac == 0.:
            self._set_default_wave(is_init=is_init)
            return "Wave set to default"
        error = None
        vA = 0
        prevA = 0
        delta0 = 0
        delta1 = 1
        w = [1, 1, 1, 1]
        x = [255, 255, 255]
        seg = 0
        bitVal = 0
        deltaA = 0
        reg = 0
        # configure MSLUTSTART
        self.fields.set_field("MSLUTSTART", (TMC_WAVE_AMP << 16))
        for i in range(256):
            if (i & 31) == 0:
                reg = 0
            if fac == 0.:
                vA = int((TMC_WAVE_AMP + 1) * math.sin(
                    (2*math.pi*i + math.pi)/1024) + .5) - 1
            else:
                # Prusa corrected wave
                vA = int(
                    TMC_WAVE_AMP * math.pow(math.sin(
                        2*math.pi*i/1024), fac) + .5)
            deltaA = vA - prevA
            prevA = vA
            bitVal = -1
            if deltaA == delta0:
                bitVal = 0
            elif deltaA == delta1:
                bitVal = 1
            else:
                if deltaA < delta0:
                    # switch w bit down
                    bitVal = 0
                    if deltaA == -1:
                        delta0 = -1
                        delta1 = 0
                        w[seg+1] = 0
                    elif deltaA == 0:
                        delta0 = 0
                        delta1 = 1
                        w[seg+1] = 1
                    elif deltaA == 1:
                        delta0 = 1
                        delta1 = 2
                        w[seg+1] = 2
                    else:
                        bitVal = -1
                    if bitVal >= 0:
                        x[seg] = i
                        seg += 1
                elif deltaA > delta1:
                    # switch w bit up
                    bitVal = 1
                    if deltaA == 1:
                        delta0 = 0
                        delta1 = 1
                        w[seg+1] = 1
                    elif deltaA == 2:
                        delta0 = 1
                        delta1 = 2
                        w[seg+1] = 2
                    elif deltaA == 3:
                        delta0 = 2
                        delta1 = 3
                        w[seg+1] = 3
                    else:
                        bitVal = -1
                    if bitVal >= 0:
                        x[seg] = i
                        seg += 1
            if bitVal < 0:
                # delta out of range
                error = "TMC2130: Error setting Sine Wave, Delta Out of Range"
                break
            if seg > 3:  # TODO: should this be greater than 2?
                # segment out of range
                error = "TMC2130: Error setting Sine Wave, Seg Out of Range"
                break
            if bitVal == 1:
                reg |= 0x80000000
            if (i & 31) == 31:
                # configure MSLUT
                name = "MSLUT" + str(((i >> 5) & 7))
                self.fields.set_field(name, reg)
            else:
                reg >>= 1
        # configure MSLUTSEL
        self.fields.set_field(
            "MSLUTSEL", w[0] | (w[1] << 2) | (w[2] << 4) | (w[3] << 6) |
            (x[0] << 8) | (x[1] << 16) | (x[2] << 24))
        if error:
            logging.error(error)
            return error
        else:
            if not is_init:
                for r in self.WAVE_REGS:
                    self.set_register(r, self.regs[r])
            success_msg = "TMC2130: Wave factor on stepper [%s] set to: %f" % \
                          (self.name, fac)
            logging.info(success_msg)
            return success_msg
    cmd_TMC_SET_STEALTH_help = "Toggle stealtchop mode"
    def cmd_TMC_SET_STEALTH(self, gcmd):
        enable = gcmd.get('ENABLE', None)
        vel = gcmd.get_int('THRESHOLD', None)
        if enable is not None:
            enable = enable.upper()
            if enable not in ["TRUE", "FALSE"]:
                gcmd.respond_info("Unknown value for ENABLE, aborting")
                return
            en = (enable == "TRUE")
            self.fields.set_field("en_pwm_mode", en)
            self.set_register("GCONF", self.regs["GCONF"])
        if vel is not None:
            sc_thrs = self.velocity_to_thrs(vel)
            self.fields.set_field("TPWMTHRS", sc_thrs)
            self.set_register("TPWMTHRS", self.fields.get_field("TPWMTHRS"))
    cmd_TMC_SET_PWMCONF_help = "Set stealthchop pwm configuration"
    def cmd_TMC_SET_PWMCONF(self, gcmd):
        ampl = gcmd.get_int('AMPL', None, minval=0, maxval=255)
        grad = gcmd.get_int('GRAD', None, minval=0, maxval=255)
        freq = gcmd.get_int('FREQ', None, minval=0, maxval=3)
        auto = gcmd.get('AUTOSCALE', None)
        if ampl is not None:
            self.fields.set_field("PWM_AMPL", ampl)
        if grad is not None:
            self.fields.set_field("PWM_GRAD", grad)
        if freq is not None:
            self.fields.set_field("pwm_freq", freq)
        if auto is not None:
            if auto.upper() not in ["TRUE", "FALSE"]:
                gcmd.respond_info(
                    "AUTOSCALE must be True or False.")
                return
            pwm_scale = (auto.upper() == "TRUE")
            self.fields.set_field("pwm_autoscale", pwm_scale)
        self.set_register("PWMCONF", self.regs["PWMCONF"])
    cmd_TMC_SET_WAVE_help = "Set wave correction factor for TMC2130 driver"
    def cmd_TMC_SET_WAVE(self, gcmd):
        is_default = gcmd.get('SET_DEFAULT', "False").upper()
        if is_default == "TRUE":
            msg = self._set_wave(0., True)
        else:
            msg = self._set_wave(gcmd.get_float('FACTOR'))
        gcmd.respond_info(msg)
    cmd_TMC_SET_STEP_help = "Force a stepper to a specified step"
    def cmd_TMC_SET_STEP(self, gcmd):
        force_move = self.printer.lookup_object('force_move')
        move_params = {'STEPPER': self.name}
        if self._stepper is None:
            logging.info(
                "TMC2130 %s: No stepper assigned, cannot step"
                % (self.name))
            gcmd.respond_info("Unable to move stepper, unknown stepper ID")
            return
        elif self._stepper.need_motor_enable:
            gcmd.respond_info("Cannot Move, motors off")
            return
        max_step = 4 * self.get_microsteps()
        target_step = gcmd.get_int('STEP', 0, minval=0)
        target_step &= (max_step - 1)
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.wait_moves()
        phase = self.get_phase()
        steps = target_step - phase
        logging.info(
            "TMC_SET_STEP Initial values: target step: "
            "%d, phase: %d, steps to move: %d"
            % (target_step, phase, steps))
        direction = 1 if self.inverted else -1
        if steps < 0:
            direction *= -1
            steps *= -1
        if steps > (max_step / 2):
            direction *= -1
            steps = max_step - steps
        distance = self._stepper.get_step_dist() * steps * direction
        mcu_pos = self._stepper.get_commanded_position()
        # Move stepper to requested step in sine wave table
        move_params['DISTANCE'] = distance
        move_params['VELOCITY'] = 5
        force_move.cmd_FORCE_MOVE(move_params)
        toolhead.wait_moves()
        self._stepper.set_commanded_position(mcu_pos)
        # Check MSCNT
        phase = self.get_phase()
        if phase != target_step:
            gcmd.respond_info("Unable to move to correct step")
            logging.info(
                "TMC2130 %s: TMC_SET_STEP Invalid MSCNT: %d, Target: %d" %
                (self.name, phase, target_step))
        else:
            gcmd.respond_info("Correctly moved to step %d:" % target_step)

######################################################################
# TMC2130 printer object
######################################################################

class TMC2130:
    def __init__(self, config):
        # Setup mcu communication
        self.fields = tmc.FieldHelper(Fields, SignedFields, FieldFormatters)
        self.mcu_tmc = MCU_TMC_SPI(config, Registers, self.fields)
        # Setup Current
        cur_helper = TMCCurrentHelper(config, self.mcu_tmc)
        self.get_current = cur_helper.get_current
        self.set_current = cur_helper.set_current
        # Allow virtual pins to be created
        tmc.TMCVirtualPinHelper(config, self.mcu_tmc)
        # Register commands
        cmdhelper = tmc.TMCCommandHelper(config, self.mcu_tmc)
        cmdhelper.setup_register_dump(ReadRegisters)
        # Setup basic register values
        mh = tmc.TMCMicrostepHelper(config, self.mcu_tmc)
        self.get_microsteps = mh.get_microsteps
        self.get_phase = mh.get_phase
        tmc.TMCStealthchopHelper(config, self.mcu_tmc, TMC_FREQUENCY)
        # Allow other registers to be set from the config
        set_config_field = self.fields.set_config_field
        set_config_field(config, "toff", 4)
        set_config_field(config, "TBL", 1)
        set_config_field(config, "IHOLDDELAY", 8)
        set_config_field(config, "TPOWERDOWN", 0)
        set_config_field(config, "PWM_AMPL", 128)
        set_config_field(config, "PWM_GRAD", 4)
        set_config_field(config, "pwm_freq", 1)
        set_config_field(config, "pwm_autoscale", True)
        set_config_field(config, "sgt", 0)

        # CHOPCONF
        ch_modes = {'SpreadCycle': 0, 'ConstantOff': 1}
        chm = config.getchoice(
            'chopper_mode', ch_modes, "SpreadCycle")
        self.fields.set_field("chm", chm)
        if chm == ch_modes['SpreadCycle']:
            set_config_field(config, "hstrt", 0)
            set_config_field(config, "hend", 7)
        else:
            tfd = config.getint('driver_TFD', 0, minval=0, maxval=15)
            self.fields.set_field('hstrt', tfd)
            self.fields.set_field('fd3', (tfd >> 3) & 1)
            hend = config.getint('driver_OFFSET', 0)
            self.fields.set_field('hend', hend)
        set_config_field(config, "disfdcc", False)
        set_config_field(config, "rndtf", False)
        set_config_field(config, "vhighfs", False)
        set_config_field(config, "vhighchm", False)
        set_config_field(config, "sync", 0)
        set_config_field(config, "dedge", False)
        set_config_field(config, "diss2g", False)

        # COOLCONF
        set_config_field(config, "semin", 0)
        seup_choices = {'1': 0, '2': 1, '4': 2, '8': 3}
        seup = config.getchoice("seup", seup_choices, '1')
        self.fields.set_field("seup", seup)
        set_config_field(config, "semax", 0)
        sedn_choices = {'32': 0, '8': 1, '2': 2, '1': 3}
        sedn = config.getchoice("sedn", sedn_choices, '32')
        self.fields.set_field("sedn", sedn)
        set_config_field(config, "seimin", 0)
        set_config_field(config, "sfilt", False)

        TMC2130_EXTRA(config, self)

def load_config_prefix(config):
    return TMC2130(config)
