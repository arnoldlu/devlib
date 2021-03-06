#    Copyright 2015 ARM Limited
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
from __future__ import division
import os
import csv
import signal
import tempfile
import struct
import subprocess

try:
    import pandas
except ImportError:
    pandas = None

from devlib.instrument import Instrument, CONTINUOUS, MeasurementsCsv
from devlib.exception import HostError
from devlib.utils.misc import which


class EnergyProbeInstrument(Instrument):

    mode = CONTINUOUS

    def __init__(self, target, resistor_values,
                 labels=None,
                 device_entry='/dev/ttyACM0',
                 ):
        super(EnergyProbeInstrument, self).__init__(target)
        self.resistor_values = resistor_values
        if labels is not None:
            self.labels = labels
        else:
            self.labels = ['PORT_{}'.format(i)
                           for i in xrange(len(resistor_values))]
        self.device_entry = device_entry
        self.caiman = which('caiman')
        if self.caiman is None:
            raise HostError('caiman must be installed on the host '
                            '(see https://github.com/ARM-software/caiman)')
        if pandas is None:
            self.logger.info("pandas package will significantly speed up this instrument")
            self.logger.info("to install it try: pip install pandas")
        self.attributes_per_sample = 3
        self.bytes_per_sample = self.attributes_per_sample * 4
        self.attributes = ['power', 'voltage', 'current']
        self.command = None
        self.raw_output_directory = None
        self.process = None

        for label in self.labels:
            for kind in self.attributes:
                self.add_channel(label, kind)

    def reset(self, sites=None, kinds=None, channels=None):
        super(EnergyProbeInstrument, self).reset(sites, kinds, channels)
        self.raw_output_directory = tempfile.mkdtemp(prefix='eprobe-caiman-')
        parts = ['-r {}:{} '.format(i, int(1000 * rval))
                 for i, rval in enumerate(self.resistor_values)]
        rstring = ''.join(parts)
        self.command = '{} -d {} -l {} {}'.format(self.caiman, self.device_entry, rstring, self.raw_output_directory)

    def start(self):
        self.logger.debug(self.command)
        self.process = subprocess.Popen(self.command,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        stdin=subprocess.PIPE,
                                        preexec_fn=os.setpgrp,
                                        shell=True)

    def stop(self):
        os.killpg(self.process.pid, signal.SIGTERM)

    def get_data(self, outfile):  # pylint: disable=R0914
        all_channels = [c.label for c in self.list_channels()]
        active_channels = [c.label for c in self.active_channels]
        active_indexes = [all_channels.index(ac) for ac in active_channels]

        num_of_ports = len(self.resistor_values)
        struct_format = '{}I'.format(num_of_ports * self.attributes_per_sample)
        not_a_full_row_seen = False
        raw_data_file = os.path.join(self.raw_output_directory, '0000000000')

        self.logger.debug('Parsing raw data file: {}'.format(raw_data_file))
        with open(raw_data_file, 'rb') as bfile:
            with open(outfile, 'wb') as wfh:
                writer = csv.writer(wfh)
                writer.writerow(active_channels)
                while True:
                    data = bfile.read(num_of_ports * self.bytes_per_sample)
                    if data == '':
                        break
                    try:
                        unpacked_data = struct.unpack(struct_format, data)
                        row = [unpacked_data[i] / 1000 for i in active_indexes]
                        writer.writerow(row)
                    except struct.error:
                        if not_a_full_row_seen:
                            self.logger.warn('possibly missaligned caiman raw data, row contained {} bytes'.format(len(data)))
                            continue
                        else:
                            not_a_full_row_seen = True
        return MeasurementsCsv(outfile, self.active_channels)
