# Probe Temp Compensation Support
#
# Copyright (C) 2018  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import thermistor
import pickle

Z_LIFT = 5.
Z_SPEED = 10.

class ProbeTemp:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.display = None
        self.sensor_type = config.get('sensor_type', None)
        if self.sensor_type is None:
            raise self.config.error("ProbeTemp: sensor_type is a required field")
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
            'CALIBRATE_PROBE_TEMP', self.cmd_CALIBRATE_PROBE_TEMP, 
            desc=self.cmd_CALIBRATE_PROBE_TEMP_help)
    def printer_state(self, state):
        if state == 'ready':
            self.probe = self.printer.lookup_object('probe')
            try:
                self.display = self.printer.lookup_object('display')
            except:
                # Display not available.  Its not necessary, only used for feedback
                self.display = None 
            if self.sensor is None:
                # A sensor was added to config but not found in the default sensor dictoinary.
                # Check to see if it is a custom thermistor.
                custom_thermistor = self.printer.lookup_object(self.sensor_type)
                self.sensor = custom_thermistor.create(self.config)
                if self.sensor:
                    self.sensor.setup_minmax(0., 100.)
                    self.sensor.setup_callback(self.temperature_callback)
    def temperature_callback(self, readtime, temp):
        # TODO: I may want to use this to access a reactor timer
        # When implementing gcodes to wait for temperature
        self.sensor_temp = temp
    def _pause_for_temp(self, toolhead, next_temp):
        total_time = 0
        while self.sensor_temp < next_temp:
                self._pause_for_time(toolhead, 5)
                total_time += 5
                if total_time >= 300:
                    # 5 minute timeout between reads
                    return False
        return True
    def _pause_for_time(self, toolhead, dwell_time):
        for i in range(dwell_time):
            toolhead.dwell(1.)
            toolhead.wait_moves()
            self.gcode.respond("Probe Temp: %.2f" % (self.sensor_temp))
    def _next_probe(self, toolhead, kinematics):
        self._move_toolhead_z(toolhead, Z_LIFT)
        self.gcode.run_script("PROBE")
        toolhead.wait_moves()
        z_pos = kinematics.get_position()[2]
        return z_pos
    def _move_toolhead_z(self, toolhead, z_pos, relative=False):
        current_pos = toolhead.get_position()
        if relative:
            current_pos[2] += z_pos
        else:
            current_pos[2] = z_pos
        toolhead.move(current_pos, Z_SPEED)
    cmd_GET_PROBE_TEMP_help = "Return the probe temperature if it has a thermistor"
    def cmd_GET_PROBE_TEMP(self, params):
        self.gcode.respond_info("Probe Temperature: %.2f" % (self.sensor_temp))
    cmd_CALIBRATE_PROBE_TEMP_help = "Calbrate the probe's offset based on its temperature"
    def cmd_CALIBRATE_PROBE_TEMP(self, params):
        z_min = -1 * (self.probe.z_offset - .15)
        max_probe_temp = self.gcode.get_float('MAX_TEMP', params, 45., above=25.)
        bed_temp = self.gcode.get_float('BED_TEMP', params, 70., above=50.)
        extruder_temp = self.gcode.get_float('EXTRUDER_TEMP', params, None, above=170.)
        toolhead = self.printer.lookup_object('toolhead')
        kin = toolhead.get_kinematics()
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
        while self.sensor_temp < max_probe_temp and keep_alive:
            z_pos = self._next_probe(toolhead, kin)
            probe_dict[self.sensor_temp] = z_pos - self.probe.z_offset
            self.gcode.respond("Probe Temp: %.2f, Z-Position: %.4f" % (self.sensor_temp, z_pos))
            if self.display:
                self.display.set_message("P: %.2f, Z: %.2f" % (self.sensor_temp, z_pos), 5.)
            # Lower Head to absorb maximum heat
            self._move_toolhead_z(toolhead, z_min, True)
            
            if self.sensor_temp >= 40.:
                keep_alive = self._pause_for_temp(toolhead, self.sensor_temp + .5)
                if not keep_alive and ex_temp_bump:
                    # After a 5 minute timeout of being unable to reach the next
                    # temp, try bumping the extruder temperature to 240
                    keep_alive = True
                    self.gcode.run_script("M104 S%.2f" % (ex_temp_bump))
                    ex_temp_bump = None
            else:
                self._pause_for_time(toolhead, 1)
        self.gcode.respond_info("Probe Calibration Complete!")
        if self.display:
            self.display.set_message("PINDA Cal Done!", 10.)
        # turn off temps, raise Z
        self.gcode.run_script("M104 S0")
        self.gcode.run_script("M140 S0")
        self.gcode.run_script("G1 Z50")
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
        toolhead.wait_moves()
        toolhead.motor_off()

def load_config(config):
    return ProbeTemp(config)
