# Probe Temp Compensation Support
#
# Copyright (C) 2018  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, math
import thermistor
import pickle

Z_LIFT = 5.
Z_SPEED = 10.
TIMEOUT = 180

class ProbeTemp:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.display = None
        self.cal_helper = ProbeCalibrationHelper(self)
        self.sensor_type = config.get('sensor_type', None)
        if self.sensor_type is None:
            raise self.config.error("ProbeTemp: sensor_type is a required field")
        self.probe_offsets = None
        offsets = self.config.get('t_offsets', None)
        if offsets:
            offsets = offsets.split('\n')
            try:
                offsets = [line.split(',', 1) for line in offsets if line.strip()]
                self.probe_offsets = [(float(p[0].strip()), float(p[1].strip()))
                                      for p in offsets]
            except:
                raise config.error("Unable to parse probe offsets in %s" % (
                    config.get_name()))
        self.sensor = None
        self.sensor_temp = 0.
        if self.sensor_type in thermistor.Sensors:
            params = thermistor.Sensors[self.sensor_type]
            self.sensor = thermistor.Thermistor(config, params)
            self.sensor.setup_minmax(0., 100.)
            self.sensor.setup_callback(self.temperature_callback)
        self.gcode.register_command(
            'GET_PROBE_TEMP', self.cmd_GET_PROBE_TEMP, desc=self.cmd_GET_PROBE_TEMP_help)
        self.gcode.register_command(
            'PROBE_WAIT', self.cmd_PROBE_WAIT_TEMP, desc=self.cmd_PROBE_WAIT_TEMP_help)
    def printer_state(self, state):
        if state == 'ready':
            self.cal_helper.printer_state(state)
            self.toolhead = self.printer.lookup_object('toolhead')
            if self.sensor is None:
                # A sensor was added to config but not found in the default sensor dictoinary.
                # Check to see if it is a custom thermistor.
                custom_thermistor = self.printer.lookup_object(self.sensor_type)
                self.sensor = custom_thermistor.create(self.config)
                if self.sensor:
                    self.sensor.setup_minmax(0., 100.)
                    self.sensor.setup_callback(self.temperature_callback)
    def temperature_callback(self, readtime, temp):
        self.sensor_temp = temp
    def get_z_offset(self):
        if self.probe_offsets:
            last_idx = len(self.probe_offsets - 1)
            if self.sensor_temp <= self.probe_offsets[0]:
                # Don't attempt to interpolate above or below
                return 0.
            elif self.sensor_temp >= self.probe_offsets[last_idx]:
                return self.probe_offsets[last_idx][1]
            else:
                # Interpolate between points, not over the entire curve, because the
                # change is not linear
                for index in range(last_idx - 1):
                    if self.sensor_temp > self.probe_offsets[index][0] and \
                       self.sensor_temp <= self.probe_offsets[index+1][0]:
                        temp_delta = self.probe_offsets[index+1][0] - self.probe_offsets[index][0]
                        t = (self.sensor_temp - self.probe_offsets[index][0]) / (temp_delta)
                        return (1. - t) * self.probe_offsets[index][1] + t * self.probe_offsets[index+1][1] 
        else:
            return 0.
    def pause_for_temp(self, next_temp, timeout=300):
        total_time = 0
        while self.sensor_temp < next_temp:
                self.pause_for_time(1)
                total_time += 1
                if timeout and total_time >= timeout:
                    return False
        return True
    def pause_for_time(self, dwell_time):
        for i in range(dwell_time):
            self.toolhead.dwell(1.)
            self.toolhead.wait_moves()
            self.gcode.respond("Probe Temp: %.2f" % (self.sensor_temp))
    def _get_heater_status(self):
        extruder = self.printer.lookup_object('extruder0').get_heater()
        bed = self.printer.lookup_object('heater_bed')
        reactor = self.printer.get_reactor()
        eventtime = reactor.monotonic()
        e_status = extruder.get_status(eventtime)
        b_status = bed.get_status(eventtime)
        return e_status['target'] > 0., b_status['target'] > 0.
    cmd_GET_PROBE_TEMP_help = "Return the probe temperature if it has a thermistor"
    def cmd_GET_PROBE_TEMP(self, params):
        self.gcode.respond_info("Probe Temperature: %.2f" % (self.sensor_temp))
    cmd_PROBE_WAIT_TEMP_help = "Pause until the probe thermistor reaches a temperature"
    def cmd_PROBE_WAIT_TEMP(self, params):
        extr_on, bed_on = self._get_heater_status()
        wait_temp = self.gcode.get_float('TEMP', params, 35., above=25., maxval=65.)
        timeout = self.gcode.get_int('TIMEOUT', params, 0, minval=0) * 60
        direction = self.gcode.get_str('DIRECTION', params, 'up').lower()
        if direction == 'down':
            if extr_on or bed_on:
                # One of the heaters are on, we can't wait
                self.gcode.respond_info("Heaters are on, please disable "
                                        "before attempting to wait for probe to cool.")
                return
            self.pause_for_temp(wait_temp, timeout)
        elif direction == 'up':
            if not extr_on and not bed_on:
                # One of the heaters are on, we can't wait
                self.gcode.respond_info("Heaters are off, please enable "
                                        "before attempting to wait for probe to heat.")
                return
            self.pause_for_temp(wait_temp, timeout)
    
class ProbeCalibrationHelper:
    def __init__(self, probetemp):
        self.sensor = probetemp
        self.printer = self.sensor.printer
        self.gcode = self.sensor.gcode
        self.display = None
        self.gcode.register_command(
            'CALIBRATE_PROBE_TEMP', self.cmd_CALIBRATE_PROBE_TEMP, 
            desc=self.cmd_CALIBRATE_PROBE_TEMP_help)
    def printer_state(self, state):
        # TODO get offset straight from stepper or maybe toolhead
        probe = self.printer.lookup_object('probe')
        self.z_offset = probe.z_offset
        self.toolhead = self.printer.lookup_object('toolhead')
        self.kinematics = self.toolhead.get_kinematics()
        try:
            self.display = self.printer.lookup_object('display')
        except:
            # Display not available.  Its not necessary, only used for feedback
            self.display = None
    def _next_probe(self):
        self._move_toolhead_z(Z_LIFT)
        self.gcode.run_script("PROBE")
        self.toolhead.wait_moves()
        z_pos = self.kinematics.get_position()[2]
        return z_pos
    def _move_toolhead_z(self, z_pos, relative=False):
        current_pos = self.toolhead.get_position()
        if relative:
            current_pos[2] += z_pos
        else:
            current_pos[2] = z_pos
        self.toolhead.move(current_pos, Z_SPEED)
    def _start_calibration(self):
        pass
    cmd_CALIBRATE_PROBE_TEMP_help = "Calbrate the probe's offset based on its temperature"
    def cmd_CALIBRATE_PROBE_TEMP(self, params):
        #TODO: Register RESUME and Cancel Gcodes
        max_probe_temp = self.gcode.get_float('MAX_TEMP', params, 45., above=25.)
        bed_temp = self.gcode.get_float('BED_TEMP', params, 70., above=50.)
        extruder_temp = self.gcode.get_float('EXTRUDER_TEMP', params, None, above=170.)
        z_pos = 0.
        ex_temp_bump = None
        probe_dict = {}
        self.gcode.respond_info("Starting Probe Temperature Calibration...")
        if self.display:
            self.display.set_message("PINDA Cal Start...")
        self.gcode.run_script("G28")
        self.gcode.run_script("G1 X50 Y50 Z150 F5000")
        self.gcode.run_script("M190 S%.2f" % (bed_temp))
        if extruder_temp:
            self.gcode.run_script("M109 S%.2f" % (extruder_temp))
            ex_temp_bump = 240. if (extruder_temp < 240.) else None
        self.gcode.run_script("G28 Z0")
        # loop probes until max_probe temp is reach
        keep_alive = True
        while self.sensor.sensor_temp < max_probe_temp and keep_alive: 
            z_pos = self._next_probe()
            probe_dict[self.sensor.sensor_temp] = z_pos - self.z_offset
            self.gcode.respond("Probe Temp: %.2f, Z-Position: %.4f" % 
                              (self.sensor.sensor_temp, z_pos))
            if self.display:
                self.display.set_message("P: %.2f, Z: %.2f" % 
                                        (self.sensor.sensor_temp, z_pos), 5.)
            # Lower Head to absorb maximum heat
            self._move_toolhead_z(.2)
            keep_alive = self.sensor.pause_for_temp(self.sensor.sensor_temp + .5, 180)
            if not keep_alive and ex_temp_bump:
                # After attempt to reach next temperature times out, 
                # try bumping the extruder temperature to 240 (NOTE: This
                # might affect e-axis geometry and the rate of probe temp increase.
                # These could have an affect on drift an thus produce unreliable
                # calibration results)
                keep_alive = True
                self.gcode.run_script("M104 S%.2f" % (ex_temp_bump))
                ex_temp_bump = None
        self.gcode.respond_info("Probe Calibration Complete!")
        if self.display:
            self.display.set_message("PINDA Cal Done!", 10.)
        # turn off temps, raise Z
        self.gcode.run_script("M104 S0")
        self.gcode.run_script("M140 S0")
        self.gcode.run_script("G1 Z50")
        #TODO: Instead of saving to python dict
        # Save dictionary to file
        try:
            f = open("/home/pi/debug_dir/PindaTemps.pkl", "wb")
        except:
            f = None
            self.gcode.respond_info("Unable to open file to dump pickled dict")
        if f:
            pickle.dump(probe_dict, f)
            f.close()
        # Wait for Z to raize and turn off motors
        self.toolhead.wait_moves()
        self.toolhead.motor_off()

def load_config(config):
    return ProbeTemp(config)
