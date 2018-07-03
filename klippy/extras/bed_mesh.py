# Mesh Bed Leveling
#
# Copyright (C) 2018 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import math
import probe
import json

# TODO: should probably move this to mathutil
def constrain(val, min_val, max_val):
    return min(max_val, max(min_val, val))

class BedMesh:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.z_adjust = 0.
        self.calibrate = BedMeshCalibrate(config, self)
        self.z_mesh = None
        self.toolhead = None
        self.horizontal_move_z = config.getfloat('horizontal_move_z', 5.)
        fade_start = config.getfloat('fade_start', 1., minval=0.)
        self.fade_end = config.getfloat('fade_end', 10.)
        self.gcode = self.printer.lookup_object('gcode')
        self.splitter = MoveSplitter(config, self.gcode, fade_start, 
                                     self.fade_end)
        if self.fade_end <= fade_start:
            # Never Fade Mesh
            self.fade_end = 999999
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
    def set_mesh(self, mesh):
        # Assign the current mesh.  If set to None, no transform
        # is applied
        self.z_mesh = mesh
        self.splitter.set_mesh(mesh)
        if mesh is None:
            self.z_adjust = 0.
    def get_position(self):
        # Return last, non-transformed position
        if self.z_mesh is None:
            # No mesh calibrated, so simply send toolhead position
            return self.toolhead.get_position()
        else:
            # return current position minus the current z-adjustment
            x, y, z, e = self.toolhead.get_position()
            return [x, y, z - self.z_adjust, e]
    def move(self, newpos, speed):
        fade_done = ((newpos[2] - self.gcode.base_position[2]) >= self.fade_end)
        if self.z_mesh is None or fade_done:
            # No mesh calibrated, or mesh leveling phased out.
            self.z_adjust = 0.
            self.toolhead.move(newpos, speed)
        else:
            prev_pos = self.get_position()
            self.splitter.build_move(prev_pos, newpos)
            while not self.splitter.traverse_complete:
                split_move = self.splitter.split()
                if split_move:
                    self.z_adjust = self.splitter.z_offset
                    self.toolhead.move(split_move, speed)
                else:
                    self.gcode.respond_error("Mesh Leveling: Error splitting move ")
    cmd_BED_MESH_OUTPUT_help = "Retrieve interpolated grid of probed z-points"
    def cmd_BED_MESH_OUTPUT(self, params):
        if self.z_mesh is None:
            self.gcode.respond_info("Bed has not been probed")
        else:
            msg = "Mesh Leveling Probed Z positions:\n"
            for line in self.calibrate.probed_z_table:
                for x in line:
                    msg += " %f" % x
                msg += "\n"
            logging.info(msg)
            self.gcode.respond_info(msg)
            msg = "Num X,Y: %d,%d\n" % (self.z_mesh.mesh_x_count, self.z_mesh.mesh_y_count) 
            msg +="Search Height: %d\n" % (self.horizontal_move_z)
            msg +="Interpolation Algorithm: %s\n" % (self.z_mesh.probe_params['algo'])
            msg += "Measured points:\n"
            for y_line in range(self.z_mesh.mesh_x_count - 1, -1, -1):
                for z in self.z_mesh.mesh_z_table[y_line]:
                    msg += "  %f" % (z)
                msg += "\n"
            self.gcode.respond(msg)

class BedMeshCalibrate:
    ALGOS = ['lagrange', 'bicubic']
    def __init__(self, config, bedmesh):
        self.printer = config.get_printer()
        self.bedmesh = bedmesh
        self.probed_z_table = None
        self.use_meshmap = False
        self.probe_params = {}
        self.probe_x_count = 3
        self.probe_y_count = 3
        points = config.get('points', None)
        if points is None:
            points = self._get_default_points()
        else:
            points = self._get_config_points(config, points)
        self._init_probe_params(config, points)
        self.probe_helper = probe.ProbePointsHelper(config, self, points)
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
    def _get_config_points(self, config, points):
        points = points.split('\n')
        try:
            points = [line.split(',', 1) for line in points if line.strip()]
            points = [(float(p[0].strip()), float(p[1].strip()))
                                    for p in points]
        except:
            raise config.error("bed_mesh: Unable to parse probe points in %s" % (
                config.get_name()))
        if len(points) < 9:
            raise config.error("bed_mesh: Need at least 9 points for %s" % (
                config.get_name()))
        # Validate points
        x_points = []
        y_points = []
        for pt in points:
            if pt[0] not in x_points:
                x_points.append(pt[0])
            if pt[1] not in y_points:
                y_points.append(pt[1])
        self.probe_x_count = len(x_points)
        self.probe_y_count = len(y_points)
        logging.info("bed_mesh: %d X rows, %d Y columns" % 
                    (self.probe_x_count, self.probe_y_count))
        if self.probe_x_count < 3:
            raise config.error("bed_mesh: Requires a minimum 3 X probe points")
        if self.probe_y_count < 3:
            raise config.error("bed_mesh: Requires a minimum 3 Y probe points")
        if (self.probe_x_count * self.probe_y_count) != len(points):
            raise config.error("bed_mesh: Point pattern is malformed")
        # Verify Zig-Zag pattern
        x_points.sort()
        y_points.sort()
        for i, pt in enumerate(points):
            y_idx = i / self.probe_x_count
            x_idx = i % self.probe_x_count
            if y_idx % 2:
               x_idx = (self.probe_x_count - 1) - x_idx
            if pt[0] != x_points[x_idx] or pt[1] != y_points[y_idx]:
                raise config.error("bed_mesh: points not in a zig_zag pattern")
        # Verify equidistance
        x_dist = x_points[1] - x_points[0]
        y_dist = y_points[1] - y_points[0]
        for i in range(1, self.probe_x_count-1, 1):
            if (x_points[i + 1] - x_points[i]) != x_dist:
                raise config.error("bed_mesh: X points not equidistant")
        for i in range(1, self.probe_y_count-1, 1):
            if (y_points[i + 1] - y_points[i]) != y_dist:
                raise config.error("bed_mesh: Y points not equidistant")
        logging.info('bed_mesh: Probe Points Validated')
        return points
    def _get_default_points(self):
        BED_ZERO_REF_X = 2.
        BED_ZERO_REF_Y = 9.4
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
    def _init_probe_params(self, config, points):
        min_x = points[0][0]
        max_x = points[0][0]
        min_y = points[0][1]
        max_y = points[0][1]
        for point in points:
            self.probe_params['min_x'] = min(min_x, point[0])
            self.probe_params['max_x'] = max(max_x, point[0])
            self.probe_params['min_y'] = min(min_y, point[1])
            self.probe_params['max_y'] = max(max_y, point[1])
        self.probe_params['x_count'] = self.probe_x_count
        self.probe_params['y_count'] = self.probe_y_count
        self.probe_params['x_offset'] = config.getfloat('probe_x_offset', 24.)
        self.probe_params['y_offset'] = config.getfloat('probe_y_offset', 5.)
        self.probe_params['mesh_x_pps'] = config.getint('mesh_x_pps', None, minval=0)
        self.probe_params['mesh_y_pps'] = config.getint('mesh_y_pps', None, minval=0)
        self.probe_params['algo'] = config.get('algorithm', 'lagrange').strip().lower()
        if self.probe_params['algo'] not in self.ALGOS:
            raise config.error("bed_mesh: Unknown algorithm <%s>" %
                              (self.probe_params['algo']))
        self.probe_params['tension'] = config.getfloat('bicubic_tension', .2, 
                                                        minval=0., maxval=2.)
    cmd_BED_MESH_CALIBRATE_help = "Perform Mesh Bed Leveling"
    def cmd_BED_MESH_CALIBRATE(self, params):
        self.use_meshmap = bool(self.gcode.get_int("MESHMAP", params, 0,
                                                minval=0, maxval=1))
        self.bedmesh.set_mesh(None)
        self.gcode.run_script_from_command("G28")
        self.probe_helper.start_probe()
        self.gcode.run_script_from_command("G1 X0 Y0 Z0.4 F3000")
    def get_probed_position(self):
        kin = self.printer.lookup_object('toolhead').get_kinematics()
        return kin.calc_position()
    def finalize(self, z_offset, positions):
        # create a 2-D array representing the probed z-positions.  First
        # dimension is y, second dimension is x
        self.probed_z_table = [[0. for i in range(self.probe_x_count)] 
                       for j in range(self.probe_y_count)]
        # Extract probed z-positions from probed positions and add
        # them to organized list
        for i, pos in enumerate(positions):
            y_position = i / self.probe_x_count
            x_position = 0
            if y_position & 1 == 0:
                # Even y count, x probed in positive directon
                x_position = i % self.probe_x_count
            else:
                # Odd y count, x probed in the negative directon
                x_position = (self.probe_x_count - 1) - (i % self.probe_x_count)
            self.probed_z_table[y_position][x_position] = pos[2] - z_offset
        if self.use_meshmap:
            outdict = {'z_probe_offsets:' : self.probed_z_table}
            self.gcode.respond(json.dumps(outdict))
        else:
            # Generate a mesh and set it for use
            mesh = ZMesh(self.probe_params)
            mesh.build_mesh(self.probed_z_table)
            self.bedmesh.set_mesh(mesh)
            self.gcode.respond_info("Mesh Bed Leveling Complete")

class MoveSplitter:
    def __init__(self, config, gcode, fade_start, fade_end):
        self.fade_start = fade_start
        self.fade_end = fade_end
        self.fade_dist = self.fade_end - self.fade_start
        self.split_delta_z = config.getfloat('split_delta_z', .025, minval=0.01)
        self.move_check_distance = config.getfloat('move_check_distance', 5., minval=3.)
        self.z_mesh = None
        self.gcode = gcode
    def set_mesh(self, mesh):
        self.z_mesh = mesh
    def build_move(self, prev_pos, next_pos):
        self.prev_pos = tuple(prev_pos)
        self.next_pos = tuple(next_pos)
        self.current_pos = list(prev_pos)
        # Z adjustment will start fading out after Z=1mm, until it reaches z_max
        real_z = next_pos[2] - self.gcode.base_position[2]
        self.z_factor = 1. 
        if real_z > self.fade_start and self.fade_end > self.fade_start:
            self.z_factor = (self.fade_end - real_z) / self.fade_dist
        self.z_offset = self.z_factor  * \
                        self.z_mesh.get_z(self.prev_pos[0] - self.gcode.base_position[0], 
                                          self.prev_pos[1] - self.gcode.base_position[1])
        self.traverse_complete = False
        self.distance_checked = 0.
        delta_x = self.next_pos[0] - self.prev_pos[0]
        delta_y = self.next_pos[1] - self.prev_pos[1]
        delta_z = self.next_pos[2] - self.prev_pos[2]
        self.total_move_length = math.sqrt(delta_x*delta_x + delta_y*delta_y +
                                           delta_z*delta_z)
        # Check for axis movement to prevent potential rounding or precision errors
        self.is_x_move = (self.prev_pos[0] != self.next_pos[0])
        self.is_y_move = (self.prev_pos[1] != self.next_pos[1])
        self.is_z_move = (self.prev_pos[2] != self.next_pos[2])
        self.is_travel = (self.prev_pos[3] == self.next_pos[3])
    def _set_next_move(self, distance_from_prev):
        t = distance_from_prev / self.total_move_length
        if t > 1. or t < 0.:
            self.gcode.respond_error("bed_mesh: Slice distance is negative " 
                                     "or greater than entire move length")
            self.current_pos[:] = self.next_pos
            return
        if self.is_x_move:
            self.current_pos[0] = (1. - t) * self.prev_pos[0] + t * self.next_pos[0]
        if self.is_y_move:
            self.current_pos[1] = (1. - t) * self.prev_pos[1] + t * self.next_pos[1]
        if self.is_z_move:
            self.current_pos[2] = (1. - t) * self.prev_pos[2] + t * self.next_pos[2]
        # Since extrusion changes linearly through a move, same interpolation
        # can be applied to E-Axis
        if not self.is_travel:
            self.current_pos[3] = (1. - t) * self.prev_pos[3] + t * self.next_pos[3]
    def split(self):
        if not self.traverse_complete:
            if self.is_x_move or self.is_y_move:
                while (self.distance_checked + self.move_check_distance) < self.total_move_length:
                    self.distance_checked += self.move_check_distance
                    self._set_next_move(self.distance_checked)
                    next_z = self.z_factor  * \
                            self.z_mesh.get_z(self.current_pos[0] - self.gcode.base_position[0], 
                                            self.current_pos[1] - self.gcode.base_position[1])
                    if abs(next_z - self.z_offset) >= self.split_delta_z:
                        self.z_offset = next_z
                        return self.current_pos[0], self.current_pos[1], \
                            self.current_pos[2] + self.z_offset, self.current_pos[3]
            # end of move reached
            self.current_pos[:] = self.next_pos
            self.z_offset = self.z_factor  * \
                            self.z_mesh.get_z(self.current_pos[0] - self.gcode.base_position[0], 
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
    def __init__(self, params):
        self.mesh_z_table = None
        self.probe_params = params
        self.mesh_x_min = params['min_x'] + params['x_offset']
        self.mesh_x_max = params['max_x'] + params['x_offset']
        self.mesh_y_min = params['min_y'] + params['y_offset']
        self.mesh_y_max = params['max_y'] + params['y_offset']
        logging.info("bed_mesh: Mesh Min: (%.2f,%.2f) Mesh Max: (%.2f,%.2f)" %
                     (self.mesh_x_min, self.mesh_y_min,
                      self.mesh_x_max, self.mesh_y_max))
        if params['algo'] == 'bicubic':
            self.build_mesh = self._sample_bicubic
        else:
            self.build_mesh = self._sample_lagrange
        # Nummber of points to interpolate per segment
        mesh_x_pps = params['mesh_x_pps']
        mesh_y_pps = params['mesh_y_pps']
        px_cnt = params['x_count']
        py_cnt = params['y_count']
        if px_cnt == 3 and py_cnt == 3:
            # 3x3 mesh
            #default is a 7x7 mesh for a 3x3 sample
            mesh_x_mult = 3 if mesh_x_pps is None else (mesh_x_pps + 1)
            mesh_y_mult = 3 if mesh_y_pps is None else (mesh_y_pps + 1)
            # 3x3 mesh must use lagrange upsampling
            self.build_mesh = self._sample_lagrange
        else:
            #default is a 2X Upsampling for larger meshes
            mesh_x_mult = 2 if mesh_x_pps is None else (mesh_x_pps + 1)
            mesh_y_mult = 2 if mesh_y_pps is None else (mesh_y_pps + 1)
        if mesh_x_mult == 1 and mesh_y_mult == 1:
            # No interpolation, sample the probed points directly
            self.build_mesh = self._sample_direct
        self.mesh_x_count = px_cnt * mesh_x_mult - (mesh_x_mult - 1)
        self.mesh_y_count = py_cnt * mesh_y_mult - (mesh_y_mult - 1)
        self.x_mult = mesh_x_mult
        self.y_mult = mesh_y_mult
        logging.info("bed_mesh: Mesh size - X:%d, Y:%d" % 
                    (self.mesh_x_count, self.mesh_y_count))
        self.mesh_x_dist = (self.mesh_x_max - self.mesh_x_min) / (self.mesh_x_count - 1)
        self.mesh_y_dist = (self.mesh_y_max - self.mesh_y_min) / (self.mesh_y_count - 1)
    def get_x_coordinate(self, index):
        return self.mesh_x_min + self.mesh_x_dist * index
    def get_y_coordinate(self, index):
        return self.mesh_y_min + self.mesh_y_dist * index
    def get_z(self, x, y):
        if self.mesh_z_table:
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
    def _sample_direct(self, probed_z_table):
        self.mesh_z_table = probed_z_table
    def _sample_lagrange(self, z_table):
        # should work for any number of probe points
        x_mult = self.x_mult
        y_mult = self.y_mult
        self.mesh_z_table = \
            [[0. if ((i % x_mult) or (j % y_mult))
            else z_table[j/y_mult][i/x_mult]
            for i in range(self.mesh_x_count)]
            for j in range(self.mesh_y_count)]
        xpts, ypts = self._get_lagrange_coords(z_table)
        # Interpolate X coordinates
        for i in range(self.mesh_y_count):
            #only interpolate X-rows that have real coordinates
            if i % y_mult !=0:
                continue
            for j in range(self.mesh_x_count):
                if j % x_mult == 0:
                    continue
                x = self.get_x_coordinate(j)
                self.mesh_z_table[i][j] = self._calc_lagrange(xpts, x, i, 1)
        #Interpolate Y coordinates
        for i in range(self.mesh_x_count):
            for j in range(self.mesh_y_count):
                if j % y_mult == 0:
                    continue
                y = self.get_y_coordinate(j)
                self.mesh_z_table[j][i] = self._calc_lagrange(ypts, y, i, 0)
    def _get_lagrange_coords(self, z_table):
        xpts = []
        ypts = []
        for i in range(self.probe_params['x_count']):
            xpts.append(self.get_x_coordinate(i * self.x_mult))
        for j in range(self.probe_params['y_count']):
            ypts.append(self.get_y_coordinate(j * self.y_mult))
        return xpts, ypts
    def _calc_lagrange(self, lpts, c, vec, xdir=1):
        pt_cnt = len(lpts)
        total = 0.
        for i in range(pt_cnt):
            n = 1.
            d = 1.
            for j in range(pt_cnt):
                if j == i:
                    continue
                n *= (c - lpts[j]) 
                d *= (lpts[i] - lpts[j])
            if xdir:
                z = self.mesh_z_table[vec][i*self.x_mult]
            else:
                z = self.mesh_z_table[i*self.y_mult][vec]
            total += z * n / d
        return total
    def _sample_bicubic(self, z_table):
        # should work for any number of probe points above 3x3
        x_mult = self.x_mult
        y_mult = self.y_mult
        c = self.probe_params['tension']
        self.mesh_z_table = \
            [[0. if ((i % x_mult) or (j % y_mult))
            else z_table[j/y_mult][i/x_mult]
            for i in range(self.mesh_x_count)]
            for j in range(self.mesh_y_count)]
        #Interpolate X values
        for y in range(self.mesh_y_count):
            if y % y_mult != 0:
                continue
            for x in range(self.mesh_x_count):
                if x % x_mult == 0:
                    continue
                pts = self._get_x_ctl_pts(x, y)
                self.mesh_z_table[y][x] = self._cardinal_spline(pts, c)
        #Interpolate Y values
        for x in range(self.mesh_x_count):    
            for y in range(self.mesh_y_count):
                if y % y_mult == 0:
                    continue
                pts = self._get_y_ctl_pts(x, y)
                self.mesh_z_table[y][x] = self._cardinal_spline(pts, c)
        logging.info("bed_mesh: Upsampled to %dx%d cubic hermite mesh" %
                    (self.mesh_x_count, self.mesh_y_count))
    def _get_x_ctl_pts(self, x,  y):
        # Fetch control points and t for a X value in the mesh
        x_mult = self.x_mult
        x_row = self.mesh_z_table[y]
        last_pt = self.mesh_x_count - 1 - x_mult
        if x < x_mult:
            p0 = p1 = x_row[0]
            p2 = x_row[x_mult]
            p3 = x_row[2*x_mult]
            t = x / float(x_mult)
        elif x > last_pt:
            p0 = x_row[last_pt - x_mult]
            p1 = x_row[last_pt]
            p2 = p3 = x_row[last_pt + x_mult]
            t = (x - last_pt) / float(x_mult)
        else:
            found = False
            for i in range(x_mult, last_pt, x_mult):
                if x > i and x < (i + x_mult):
                    p0 = x_row[i - x_mult]
                    p1 = x_row[i]
                    p2 = x_row[i + x_mult]
                    p3 = x_row[i + 2*x_mult]
                    t = (x - i) / float(x_mult)
                    found = True
                    break
            if not found:
                # TODO: raise an error
                logging.debug("bed_mesh: Error finding x control points")
                return None
        return p0, p1, p2, p3, t
    def _get_y_ctl_pts(self, x, y):
        # Fetch control points and t for a Y value in the mesh
        y_mult = self.y_mult
        last_pt = self.mesh_y_count - 1 - y_mult
        y_col = self.mesh_z_table
        if y < y_mult:
            p0 = p1 = y_col[0][x]
            p2 = y_col[y_mult][x]
            p3 = y_col[2*y_mult][x]
            t = y / float(y_mult)
        elif y > last_pt:
            p0 = y_col[last_pt - y_mult][x]
            p1 = y_col[last_pt][x]
            p2 = p3 = y_col[last_pt + y_mult][x]
            t = (y - last_pt)/ float(y_mult)
        else:
            found = False
            for i in range(y_mult, last_pt, y_mult):
                if y > i and y < (i + y_mult):
                    p0 = y_col[i - y_mult][x]
                    p1 = y_col[i][x]
                    p2 = y_col[i + y_mult][x]
                    p3 = y_col[i + 2*y_mult][x]
                    t = (y - i) / float(y_mult)
                    found = True
                    break
            if not found:
                # TODO: raise an error
                logging.debug("bed_mesh: Error finding y control points")
                return None
        return p0, p1, p2, p3, t
    def _cardinal_spline(self, p, tension):
        t = p[4]
        t2 = t*t
        t3 = t2*t
        m1 = tension * (p[2] - p[0])
        m2 = tension * (p[3] - p[1])
        a = p[1] * (2*t3 - 3*t2 + 1)
        b = p[2] * (-2*t3 + 3*t2)
        c = m1 * (t3 - 2*t2 + t)
        d = m2 * (t3 - t2)
        return a + b + c + d

def load_config(config):
    return BedMesh(config)
