#!/usr/bin/env python3
# vim: set encoding=utf-8 tabstop=4 softtabstop=4 shiftwidth=4 expandtab
#########################################################################
#  Copyright 2025-      sismaiwe                         miwe77@gmail.com
#########################################################################
#  This file is part of SmartHomeNG.
#  https://www.smarthomeNG.de
#  https://knx-user-forum.de/forum/supportforen/smarthome-py
#
#  SmartHomeNG is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  SmartHomeNG is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with SmartHomeNG. If not, see <http://www.gnu.org/licenses/>.
#
#########################################################################

import socket
import datetime

from lib.model.smartplugin import SmartPlugin
from lib.item import Items

from .webif import WebInterface

STATUS_START1 = b'\x0e'
STATUS_START2 = b'\xff'
STATUS_END = int(56)

class Ledatronic(SmartPlugin):
    """
    Main class of the Plugin. Does all plugin specific stuff and provides the update functions for the items
    """

    PLUGIN_VERSION = '1.0.0'

    def __init__(self, sh):
        """
        Initializes the plugin.

        If you need the sh object at all, use the method self.get_sh() to get it. There should be almost no need for
        a reference to the sh object any more.

        Plugins have to use the new way of getting parameter values:
        use the SmartPlugin method get_parameter_value(parameter_name). Anywhere within the Plugin you can get
        the configured (and checked) value for a parameter by calling self.get_parameter_value(parameter_name). It
        returns the value in the datatype that is defined in the metadata.
        """

        # Call init code of parent class (SmartPlugin)
        super().__init__()

        self.last_update = None
        self.socket = None
        self.parsed_data = {}
        self.parsing_errors = []

        self._pause_item_path = self.get_parameter_value('pause_item')
        self.host = self.get_parameter_value('host')
        self.port = self.get_parameter_value('port')
        self._cycle = max(self.get_parameter_value('cycle'), 10)

        self.logger.debug(f"{self.host=}")
        self.logger.debug(f"{self.port=}")

        self.init_webinterface(WebInterface)
        return

    def run(self):
        """
        Run method for the plugin
        """
        self.logger.dbghigh(self.translate("Methode '{method}' aufgerufen", {'method': 'run()'}))
        self.scheduler_add(self.get_fullname() + '_poll', self.poll_device, cycle=self._cycle)
        self.alive = True     # if using asyncio, do not set self.alive here. Set it in the session coroutine

        # let the plugin change the state of pause_item
        if self._pause_item:
            self._pause_item(False, self.get_fullname())

    def stop(self):
        """
        Stop method for the plugin
        """
        self.logger.dbghigh(self.translate("Methode '{method}' aufgerufen", {'method': 'stop()'}))
        self.alive = False     # if using asyncio, do not set self.alive here. Set it in the session coroutine

        # let the plugin change the state of pause_item
        if self._pause_item:
            self._pause_item(True, self.get_fullname())

        self.scheduler_remove_all()

    def parse_item(self, item):
        """
        Default plugin parse_item method. Is called when the plugin is initialized.
        The plugin can, corresponding to its attribute keywords, decide what to do with
        the item in future, like adding it to an internal array for future reference
        :param item:    The item to process.
        :return:        If the plugin needs to be informed of an items change you should return a call back function
                        like the function update_item down below. An example when this is needed is the knx plugin
                        where parse_item returns the update_item function when the attribute knx_send is found.
                        This means that when the items value is about to be updated, the call back function is called
                        with the item, caller, source and dest as arguments and in case of the knx plugin the value
                        can be sent to the knx with a knx write function within the knx plugin.
        """
        # check for pause item
        if item.property.path == self._pause_item_path:
            self.logger.debug(f'pause item {item.property.path} registered')
            self._pause_item = item
            self.add_item(item, updating=True)
            return self.update_item

        if self.has_iattr(item.conf, 'leda_data_point'):
            self.logger.debug(f"parse item: {item}")
            leda_data_point = self.get_iattr_value(item.conf, 'leda_data_point').lower()
            self.add_item(item, config_data_dict={'leda_data_point': leda_data_point}, updating=True)

    def parse_logic(self, logic):
        """
        Default plugin parse_logic method
        """
        if 'xxx' in logic.conf:
            # self.function(logic['name'])
            pass

    def update_item(self, item, caller=None, source=None, dest=None):
        """
        Item has been updated

        This method is called, if the value of an item has been updated by SmartHomeNG.
        It should write the changed value out to the device (hardware/interface) that
        is managed by this plugin.

        To prevent a loop, the changed value should only be written to the device, if the plugin is running and
        the value was changed outside of this plugin(-instance). That is checked by comparing the caller parameter
        with the fullname (plugin name & instance) of the plugin.

        :param item: item to be updated towards the plugin
        :param caller: if given it represents the callers name
        :param source: if given it represents the source
        :param dest: if given it represents the dest
        """
        # check for pause item
        if item is self._pause_item:
            if caller != self.get_shortname():
                self.logger.debug(f'pause item changed to {item()}')
                if item() and self.alive:
                    self.stop()
                elif not item() and not self.alive:
                    self.run()
            return

        if self.alive and caller != self.get_fullname():
            # code to execute if the plugin is not stopped
            # and only, if the item has not been changed by this plugin:
            self.logger.info(f"update_item: '{item.property.path}' has been changed outside this plugin by caller '{self.callerinfo(caller, source)}'")

            pass

    def poll_device(self):
        """
        Polls for updates of the device
        """

        self.last_update = datetime.datetime.now()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        try:
            self.socket.connect((self.host, self.port))
            self.logger.info(f"Connected to {self.host}:{self.port}")

            while True:
                # Receive the first start byte
                byte1 = self._recv_all(1)
                if byte1 != STATUS_START1:
                    self.logger.debug(f"Skipping unexpected byte: {byte1}")
                    continue

                # Receive the second start byte
                byte2 = self._recv_all(1)
                if byte2 != STATUS_START2:
                    self.logger.debug(f"Skipping sequence, unexpected byte: {byte2}")
                    continue

                # We've found the start sequence, now receive the data payload
                data = self._recv_all(STATUS_END)
                self.logger.info(f"Received complete data payload: {data}")
                self.parse_data(data)
                self.update_item_values()

        except ConnectionRefusedError:
            self.logger.warning(f"Connection refused. Is the server running on {self.host}:{self.port}?")
            raise
        except ConnectionError as e:
            self.logger.warning(f"Socket error: {e}")
            raise
        except Exception as e:
            self.logger.warning(f"An unexpected error occurred: {e}")
            raise
        finally:
            if self.socket:
                self.socket.close()
                self.logger.debug("Socket closed.")

    def _recv_all(self, num_bytes):
        """Helper to ensure all requested bytes are received."""
        buffer = bytearray()
        while len(buffer) < num_bytes:
            packet = self.socket.recv(num_bytes - len(buffer))
            if not packet:  # Connection closed or interrupted
                raise ConnectionError("Socket connection interrupted unexpectedly.")
            buffer.extend(packet)
        return buffer

    def parse_data(self, data: bytearray):

        OVEN_STATES = {0: 'Bereit',
                       1: 'Start',
                       2: 'Anheizen',
                       3: 'Anheizen',
                       4: 'Heizbetrieb',
                       5: 'Ende',
                       6: 'Pause',
                       7: 'Grundglut',
                       8: 'Grundglut nachlegen',
                       97: 'Anheizfehler',
                       98: 'Tür offen',
                       99: 'Sensorfehler',
                       }

        VENT_STATES = {
            0: "off",
            1: "on"
        }

        FIELD_DEFINITIONS = {
            "current_combustion_temp": {
                "offset": 0,
                "length": 2,
                "type": "int",
                "byteorder": "big",
                "label": "Brennraumtemperatur",
                "unit": "°C"
            },
            "air_flap_setpoint": {
                "offset": 2,
                "length": 1,
                "type": "int",
                "label": "Luftklappe Soll",
                "unit": "%"
            },
            "air_flap_actual": {
                "offset": 3,
                "length": 1,
                "type": "int",
                "label": "Luftklappe Ist",
                "unit": "%"
            },
            "status": {
                "offset": 4,
                "length": 1,
                "type": "mapped",  # Indicates this value needs a lookup
                "map": OVEN_STATES,
                "label": "Status"
            },
            "error_status": {
                "offset": 5,
                "length": 1,
                "type": "int",
                "label": "Fehlerstatus"
            },
            "output": {
                "offset": 6,
                "length": 1,
                "type": "int",
                "label": "Ausgabe"
            },
            "controller_version": {
                "offset": 7,
                "length": 1,
                "type": "int",
                "label": "Reglerversion"
            },
            "max_combustion_temp": {
                "offset": 8,
                "length": 2,
                "type": "int",
                "byteorder": "big",
                "label": "maximale Brennraumtemperatur",
                "unit": "°C"
            },
            "oven_state_raw": {
                "offset": 10,
                "length": 1,
                "type": "int",
                "label": "oven (raw value)"
            },
            "oven_state_mapped": {
                "offset": 10,
                "length": 1,
                "type": "mapped",
                "map": OVEN_STATES,
                "label": "oven (mapped state)"
            },
            "base_glow_temp": {
                "offset": 11,
                "length": 1,
                "type": "int",
                "label": "Grundgluttemperatur, berechnet",
                "unit": "°C"
            },
            "trend": {
                "offset": 12,
                "length": 1,
                "type": "int",
                "label": "Abbrandkurventrend"
            },
            "num_burn_cycles": {
                "offset": 25,
                "length": 2,
                "type": "int",
                "byteorder": "big",
                "label": "Anzahl Abbrände"
            },
            "num_heating_errors": {
                "offset": 27,
                "length": 2,
                "type": "int",
                "byteorder": "big",
                "label": "Anzahl Heizfehler (offset 15)"
            },
            "water_pocket_temp": {
                "offset": 31,
                "length": 1,
                "type": "int",
                "label": "WassertascheTemp in °C",
                "unit": "°C"
            },
            "tank_temp_bottom": {
                "offset": 34,
                "length": 1,
                "type": "int",
                "label": "TankTempUnten in °C",
                "unit": "°C"
            },
            "tank_temp_middle": {
                "offset": 35,
                "length": 1,
                "type": "int",
                "label": "TankTempMitte - Fix: 141°C no sensor",
                "unit": "°C"
            },
            "tank_temp_top": {
                "offset": 36,
                "length": 1,
                "type": "int",
                "label": "TankTempOben",
                "unit": "°C"
            },
            "forward_temp": {
                "offset": 37,
                "length": 1,
                "type": "int",
                "label": "Rücklauftemperatur",
                "unit": "°C"
            },
            "pump_power": {
                "offset": 38,
                "length": 1,
                "type": "int",
                "label": "Pumpleistung",
                "unit": "%"
            },
            "supply_temp": {
                "offset": 39,
                "length": 1,
                "type": "int",
                "label": "Vorlauftemperatur",
                "unit": "°C"
            },
            "pressure": {
                "offset": 44,
                "length": 1,
                "type": "int",
                "label": "Druck"
            },
            "exhaust_temp": {
                "offset": 46,
                "length": 2,
                "type": "int",
                "byteorder": "big",
                "label": "Abgastemperatur",
                "unit": "°C"
            },
            "fan_state": {
                "offset": 50,
                "length": 1,
                "type": "mapped",
                "map": VENT_STATES,
                "label": "Ventilator"
            },
            "lock_state": {
                "offset": 47,
                "length": 1,
                "type": "bool",
                "label": "Sperre"
            },
            "alarm_counter": {
                "offset": 48,
                "length": 1,
                "type": "int",
                "label": "Alarmzähler"
            },
            "error_offset": {
                "offset": 49,
                "length": 1,
                "type": "int",
                "label": "Erroroffset"
            },
            "error_pressure": {
                "offset": 50,
                "length": 1,
                "type": "int",
                "label": "ErrorPressure"
            },
        }

        if not isinstance(data, bytearray):
            raise TypeError("Input 'data' must be a bytearray.")

        self.parsed_data = {}  # Clear previous results
        self.parsing_errors = []  # Clear previous errors

        for field_name, field_info in FIELD_DEFINITIONS.items():
            offset = field_info["offset"]
            length = field_info["length"]
            end_offset = offset + length
            data_type = field_info["type"]
            label = field_info.get("label", field_name)  # Use label if available, else field_name

            # Check if enough data is available for this field
            if end_offset > len(data):
                self.parsing_errors.append(f"Not enough data for '{label}' (expected {end_offset} bytes, got {len(data)}). Skipping.")
                self.parsed_data[field_name] = None  # Store None if parsing fails
                continue

            raw_bytes = data[offset:end_offset]
            parsed_value = None

            try:
                if data_type == "int":
                    # For multi-byte integers, 'byteorder' is crucial.
                    # For single bytes, it doesn't matter, but it's good to specify.
                    byteorder = field_info.get("byteorder", "little")  # Default to little if not specified
                    parsed_value = int.from_bytes(raw_bytes, byteorder=byteorder)
                elif data_type == "float":
                    self.parsing_errors.append(f"Float parsing not implemented for '{label}'.")
                elif data_type == "string":
                    parsed_value = raw_bytes.decode(field_info.get("encoding", "utf-8")).strip('\0')
                elif data_type == "mapped":
                    map_dict = field_info.get("map")
                    if map_dict is not None and length == 1:  # Mapped values are typically single bytes
                        raw_byte_value = int.from_bytes(raw_bytes, byteorder="big")  # Byteorder doesn't matter for single byte
                        parsed_value = map_dict.get(raw_byte_value, f"Unknown value: {raw_byte_value}")
                    else:
                        self.parsing_errors.append(f"Missing map or invalid length for mapped type '{label}'. Raw bytes: {raw_bytes.hex()}")
                        parsed_value = raw_bytes  # Keep raw if mapping fails
                elif data_type == "bool":
                    parsed_value = bool(raw_bytes)
                # Add more data types as needed (e.g., 'bool', 'bitmask', etc.)
                else:
                    self.parsing_errors.append(f"Unsupported data type '{data_type}' for '{label}'.")
                    parsed_value = raw_bytes  # Keep raw bytes if type is unknown

                self.parsed_data[field_name] = parsed_value

            except Exception as e:
                self.parsing_errors.append(f"Error parsing '{label}' (offset {offset}, length {length}): {e}. Raw bytes: {raw_bytes.hex()}")
                self.parsed_data[field_name] = None  # Or the raw bytes, depending on preference

        current_combustion_temp = self.parsed_data.get("current_combustion_temp")
        self.parsed_data['active'] = isinstance(current_combustion_temp, (int, float)) and current_combustion_temp > 30

        self.logger.info(f"{self.parsed_data=}")
        self.logger.info(f"{self.parsing_errors=}")

    def update_item_values(self):

        # get relevant item list concerning dedicated device
        device_item_list = self.get_item_list()

        # loop through item list and get values from dict
        for item in device_item_list:
            item_config = self.get_item_config(item)
            leda_data_point = item_config.get('leda_data_point')
            if leda_data_point is None:
                continue
            value = self.parsed_data.get(leda_data_point)
            if value:
                item_config['value'] = value
                item(value, self.get_shortname())
