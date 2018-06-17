# Mesh Bed Leveling
#
# Copyright (C) 2018 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import math
import probe, mathutil

# TODO: should probably move this to mathutil
def constrain(val, min_val, max_val):
    return min(max_val, max(min_val, val))

# TODO: The values below are for Prusa i3 MK3 only.  I should
# retreive these from the config, along with the actual
# probe points.
PROBE_X_COUNT = 3
PROBE_Y_COUNT = 3
BED_ZERO_REF_X = 2.
BED_ZERO_REF_Y = 9.4

# Constants For Mesh leveling Calculation
Z_FADE_START = 1.
Z_FADE_MAX = 10.
Z_FADE_DIST = Z_FADE_MAX - Z_FADE_START
Z_DELTA_THRESH = .025
MOVE_TRAVERSE_DIST = 5.

class BedMesh:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.z_adjust = 0.
        self.calibrate = BedMeshCalibrate(config, self)
        self.mesh_z = ZMesh(config)
        self.toolhead = None
        self.probed_z_table = None
        self.horizontal_move_z = config.getfloat('horizontal_move_z', 5.)
        #self.probe_x_count = config.getint('probe_x_count', 3)
        #self.probe_y_count = config.getint('probe_y_count' 3)
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            'BED_MESH_OUTPUT', self.cmd_BED_MESH_OUTPUT,
            desc=self.cmd_BED_MESH_OUTPUT_help)
        # Register G81 as alias
        self.gcode.register_command(
            'G81', self.cmd_BED_MESH_OUTPUT,
            desc=self.cmd_BED_MESH_OUTPUT_help)
        self.gcode.set_move_transform(self)
    def printer_state(self, state):
        if state == 'connect':
            self.toolhead = self.printer.lookup_object('toolhead')
    def set_probed_z_table(self, z_table):
        # Sets the table of probed Z-points.  Set to None to disable
        # Mesh Leveling Transformation
        self.probed_z_table = z_table
        if z_table is not None:
            self.mesh_z.build_mesh(z_table)
        else:
            self.mesh_z_positions = None
    def get_position(self):
        # Return last, non-transformed position
        if self.probed_z_table is None:
            # No mesh calibrated, so simply send toolhead position
            return self.toolhead.get_position()
        else:
            # return current position minus the current z-adjustment
            x, y, z, e = self.toolhead.get_position()
            return [x, y, z - self.z_adjust, e]
    def move(self, newpos, speed):
        if self.probed_z_table is None or \
           (newpos[2] - self.gcode.base_position[2]) >= Z_FADE_MAX:
            # No mesh calibrated, or mesh leveling phased out.
            self.z_adjust = 0.
            self.toolhead.move(newpos, speed)
        else:
            prev_pos = self.get_position()
            splitter = MoveSplitter(prev_pos, newpos, self.mesh_z, self.gcode)
            while not splitter.traverse_complete:
                split_move = splitter.split()
                if split_move:
                    self.z_adjust = splitter.z_offset
                    self.toolhead.move(split_move, speed)
                else:
                    self.gcode.respond_error("Mesh Leveling: Error splitting move ")
    cmd_BED_MESH_OUTPUT_help = "Retreive interpolated grid of probed z-points"
    def cmd_BED_MESH_OUTPUT(self, params):
        if self.probed_z_table is None:
            self.gcode.respond_info("Bed has not been probed")
        else:
             # Temporarily respond with the 9 Z values for testing
            msg = "Mesh Leveling Probed Z positions:\n"
            for line in self.probed_z_table:
                msg += "%f %f %f\n" % (line[0], line[1], line[2])
            logging.info(msg)
            self.gcode.respond_info(msg)

            # Upsample to a 7x7 mesh
            msg = "Num X,Y: %d,%d\n" % (self.mesh_z.mesh_x_count, self.mesh_z.mesh_y_count) 
            msg +="Search Height: %d\n" % (self.horizontal_move_z)
            
            msg += "Measured points:\n"
            for y_line in range(self.mesh_z.mesh_x_count - 1, -1, -1):
                for z in self.mesh_z.mesh_z_table[y_line]:
                    msg += "  %f" % (z)
                msg += "\n"
            self.gcode.respond(msg)

class BedMeshCalibrate:
    def __init__(self, config, bedmesh):
        self.printer = config.get_printer()
        self.bedmesh = bedmesh
        #TODO: Instead of calculating probe points, add them to config
        self.probe_helper = probe.ProbePointsHelper(config, self, 
                                                    self.calculate_probe_points())
        # Automatic probe:z_virtual_endstop XY detection
        self.z_position_endstop = None
        if config.has_section('stepper_z'):
            zconfig = config.getsection('stepper_z')
            self.z_position_endstop = zconfig.getfloat('position_endstop', None)
        # Register MESH_BED_LEVING command
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command(
            'BED_MESH_CALIBRATE', self.cmd_BED_MESH_CALIBRATE,
            desc=self.cmd_BED_MESH_CALIBRATE_help)
        # Register G80 as alias
        self.gcode.register_command(
            'G80', self.cmd_BED_MESH_CALIBRATE,
            desc=self.cmd_BED_MESH_CALIBRATE_help)
    def calculate_probe_points(self):
        probe_points = [
            (13.- BED_ZERO_REF_X, 10.4 - BED_ZERO_REF_Y),
            (115.- BED_ZERO_REF_X, 10.4 - BED_ZERO_REF_Y),
            (216. - BED_ZERO_REF_X, 10.4 - BED_ZERO_REF_Y),
            (216. - BED_ZERO_REF_X, 106.4 - BED_ZERO_REF_Y),
            (115. - BED_ZERO_REF_X, 106.4 - BED_ZERO_REF_Y),
            (13. - BED_ZERO_REF_X, 106.4 - BED_ZERO_REF_Y),
            (13. - BED_ZERO_REF_X, 202.4 - BED_ZERO_REF_Y),
            (115. - BED_ZERO_REF_X, 202.4 - BED_ZERO_REF_Y),
            (216. - BED_ZERO_REF_X, 202.4 - BED_ZERO_REF_Y),
        ]
        return probe_points
    cmd_BED_MESH_CALIBRATE_help = "Perform Mesh Bed Leveling"
    def cmd_BED_MESH_CALIBRATE(self, params):
        self.bedmesh.set_probed_z_table(None)
        self.gcode.run_script("G28")
        self.probe_helper.start_probe()
        self.gcode.run_script("G1 X0 Y0 Z0.4 F3000")
    def get_position(self):
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        return kin.get_position()
    def finalize(self, z_offset, positions):
        # create a 2-D array representing the probed z-positions.  First
        # dimension is y, second dimension is x
        z_table = [[0. for i in range(PROBE_X_COUNT)] 
                       for j in range(PROBE_Y_COUNT)]
        # Extract probed z-positions from probed positions and add
        # them to organized list
        for i, pos in enumerate(positions):
            y_position = i / PROBE_X_COUNT
            x_position = 0
            if y_position & 1 == 0:
                # Even y count, x probed in positive directon
                x_position = i % PROBE_X_COUNT
            else:
                # Odd y count, x probed in the negative directon
                x_position = (PROBE_X_COUNT - 1) - (i % PROBE_X_COUNT)
            z_table[y_position][x_position] = pos[2] - z_offset
        self.bedmesh.set_probed_z_table(z_table)
        self.gcode.respond_info("Mesh Bed Leveling Complete")

class MoveSplitter:
    def __init__(self, prev_pos, next_pos, mesh_z, gcode):
        self.prev_pos = tuple(prev_pos)
        self.next_pos = tuple(next_pos)
        self.current_pos = list(prev_pos)
        self.mesh_z = mesh_z
        self.gcode = gcode
        # Z adjustment will start fading out after Z=1mm, until it reaches z_max
        real_z = next_pos[2] - self.gcode.base_position[2]
        self.z_factor = 1. if (real_z <= Z_FADE_START) else \
                              ((Z_FADE_MAX - real_z) / Z_FADE_DIST)
        self.z_offset = self.z_factor  * \
                        self.mesh_z.get_z(self.prev_pos[0] - self.gcode.base_position[0], 
                                          self.prev_pos[1] - self.gcode.base_position[1])
        self.traverse_complete = False
        self.distance_checked = 0.
        delta_x = self.next_pos[0] - self.prev_pos[0]
        delta_y = self.next_pos[1] - self.prev_pos[1]
        delta_z = self.next_pos[2] - self.prev_pos[2]
        self.total_move_length = math.sqrt(math.pow(delta_x, 2) + math.pow(delta_y, 2) +
                                           math.pow(delta_z, 2))
        # Check for z movement and extrusion to bypass any changes and prevent
        # potential rounding or precision errors
        self.is_z_move = True if (self.prev_pos[2] != self.next_pos[2]) else False
        self.is_xy_move = True if (delta_x != 0. or delta_y != 0.) else False
        self.is_travel = True if (self.prev_pos[3] == self.next_pos[3]) else False
    def _set_next_move(self, distance_from_prev):
        t = distance_from_prev / self.total_move_length
        if t > 1. or t < 0.:
            self.gcode.respond_error("Mesh Bed Leveling: Slice distance is negative " + 
                                     "or greater than entire move length")
            self.current_pos[:] = self.next_pos
            return
        self.current_pos[0] = (1. - t) * self.prev_pos[0] + t * self.next_pos[0]
        self.current_pos[1] = (1. - t) * self.prev_pos[1] + t * self.next_pos[1]
        if self.is_z_move:
            self.current_pos[2] = (1. - t) * self.prev_pos[2] + t * self.next_pos[2]
        # Since extrusion changes linearly through a move, same interpolation
        # can be applied to E-Axis
        if not self.is_travel:
            self.current_pos[3] = (1. - t) * self.prev_pos[3] + t * self.next_pos[3]
    def split(self):
        if not self.traverse_complete:
            if self.is_xy_move:
                while (self.distance_checked + MOVE_TRAVERSE_DIST) < self.total_move_length:
                    self.distance_checked += MOVE_TRAVERSE_DIST
                    self._set_next_move(self.distance_checked)
                    next_z = self.z_factor  * \
                            self.mesh_z.get_z(self.current_pos[0] - self.gcode.base_position[0], 
                                            self.current_pos[1] - self.gcode.base_position[1])
                    if abs(next_z - self.z_offset) >= Z_DELTA_THRESH:
                        self.z_offset = next_z
                        return self.current_pos[0], self.current_pos[1], \
                            self.current_pos[2] + self.z_offset, self.current_pos[3]
            # end of move reached
            self.current_pos[:] = self.next_pos
            self.z_offset = self.z_factor  * \
                            self.mesh_z.get_z(self.current_pos[0] - self.gcode.base_position[0], 
                                              self.current_pos[1] - self.gcode.base_position[1])
            # Its okay to add Z-Offset to the final move, since it will not be used
            # again.  
            self.current_pos[2] += self.z_offset
            self.traverse_complete = True
            return self.current_pos
        else:
            # Traverse complete
            return None
       
class ZMesh:
    def __init__(self, config, mesh_x_count=None, mesh_y_count=None):
        self.mesh_z_table = None
        # TODO: variables below should probably have min values
        self.mesh_x_min = config.getfloat('mesh_x_min', 35.)
        self.mesh_x_max = config.getfloat('mesh_x_max', 238.)
        self.mesh_y_min = config.getfloat('mesh_y_min', 6.)
        self.mesh_y_max = config.getfloat('mesh_y_max', 202.)
        if not mesh_x_count:
            self.mesh_x_count = config.getint('mesh_x_count', 7, minval=5)
        elif mesh_x_count < 5:
            raise config.error("mesh_x_count cannot be less than 5")
        if not mesh_y_count:
            self.mesh_y_count = config.getint('mesh_y_count', 7, minval=5)
        elif mesh_y_count < 5:
            raise config.error("mesh_y_count cannot be less than 5")
        if not (self.mesh_x_count & 1):
            raise config.error("mesh_y_count must be an odd value")
        if not (self.mesh_y_count & 1):
            raise config.error("mesh_y_count must be an odd value")
        self.mesh_x_dist = (self.mesh_x_max - self.mesh_x_min) / (self.mesh_x_count - 1)
        self.mesh_y_dist = (self.mesh_y_max - self.mesh_y_min) / (self.mesh_y_count - 1)
    def get_x_coordinate(self, index):
        return self.mesh_x_min + self.mesh_x_dist * index
    def get_y_coordinate(self, index):
        return self.mesh_y_min + self.mesh_y_dist * index
    def get_z(self, x, y):
        if self.mesh_z_table:
            # TODO: This is linear interpolation straight from Prusa's
            # implementation of mesh bed leveling.  In the future we
            # may want to look for other ways to interpolate z-coordinates
            x_index = 0
            y_index = 0
            s = 0.
            t = 0.
            x_index = int(math.floor((x - self.mesh_x_min) /self.mesh_x_dist))
            if (x_index < 0):
                x_index = 0
                s = (x -self.mesh_x_min) / self.mesh_x_dist
                s = min(s, 1.)
            elif x_index > (self.mesh_x_count - 2):
                x_index = self.mesh_x_count - 2
                s = (x - self.get_x_coordinate(x_index)) / self.mesh_x_dist
                s = max(s, 0.)
            else:
                s = (x - self.get_x_coordinate(x_index)) / self.mesh_x_dist
                s = constrain(s, 0., 1.)
            y_index = int(math.floor((y - self.mesh_y_min) / self.mesh_y_dist))
            if y_index < 0:
                y_index = 0
                t = (y - self.mesh_y_min) / self.mesh_y_dist
                t = min(t, 1.)
            elif y_index > (self.mesh_y_count - 2):
                y_index = self.mesh_y_count - 2
                t = (y - self.get_y_coordinate(y_index)) / self.mesh_y_dist
                t = max(t, 0.)
            else:
                t = (y - self.get_y_coordinate(y_index)) / self.mesh_y_dist
                t = constrain(t, 0., 1.)
            si = 1. - s
            z0 = si * self.mesh_z_table[y_index][x_index] + \
                 s * self.mesh_z_table[y_index][x_index+1]
            z1 = si * self.mesh_z_table[y_index+1][x_index] + \
                 s * self.mesh_z_table[y_index+1][x_index+1]
            return (1. - t) * z0 + t * z1
        else:
            # No mesh table generated, no z-adjustment
            return 0.
    def build_mesh(self, probed_z_table):
        self._sample_3x3(probed_z_table)
    def _sample_3x3(self, probed_z_table):
        self.mesh_z_table= [[0. for i in range(self.mesh_x_count)] 
                      for j in range(self.mesh_y_count)]
        x_center = int(self.mesh_x_count / 2)
        x_last = self.mesh_x_count - 1
        y_center = int(self.mesh_y_count / 2)
        y_last = self.mesh_y_count - 1
        #Interpolate X axis points
        x0 = self.mesh_x_min
        x1 = (self.mesh_x_min + self.mesh_x_max) * .5
        x2 = self.mesh_x_max
        for i in range(3):
            y_idx = i * y_center
            self.mesh_z_table[y_idx][0] = probed_z_table[i][0]
            self.mesh_z_table[y_idx][x_center] = probed_z_table[i][1]
            self.mesh_z_table[y_idx][x_last] = probed_z_table[i][2]
            for j in range(1, x_last):
                if j == x_center:
                    continue
                x = self.get_x_coordinate(j)
                self.mesh_z_table[y_idx][j] = \
                    probed_z_table[i][0] * (x - x1) * (x - x2) / ((x0 - x1) * (x0 - x2)) + \
                    probed_z_table[i][1] * (x - x0) * (x - x2) / ((x1 - x0) * (x1 - x2)) + \
                    probed_z_table[i][2] * (x - x0) * (x - x1) / ((x2 - x0) * (x2 - x1))
        y0 = self.mesh_y_min
        y1 = (self.mesh_y_min + self.mesh_y_max) * .5
        y2 = self.mesh_y_max
        for i in range(self.mesh_x_count):
            for j in range(1, y_last):
                if j == y_center:
                    continue
                y = self.get_y_coordinate(j)
                self.mesh_z_table[j][i] = \
                    self.mesh_z_table[0][i] * (y - y1) * (y - y2) / ((y0 - y1) * (y0 - y2)) + \
                    self.mesh_z_table[y_center][i] * (y - y0) * (y - y2) / ((y1 - y0) * (y1 - y2)) + \
                    self.mesh_z_table[y_last][i] * (y - y0) * (y - y1) / ((y2 - y0) * (y2 - y1))

def load_config(config):
    return BedMesh(config)
