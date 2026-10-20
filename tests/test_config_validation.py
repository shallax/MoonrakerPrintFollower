# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Exercise portable settings refusals, discovery and non-mutating validation.

from copy import deepcopy
from dataclasses import asdict, replace
import unittest

from mpf.settings.ConfigConstraints import RECOVERY_NUMBER_LIMITS
from mpf.settings.ConfigValidation import ConfigDiscovery, ConfigValidationError, validate_config
from mpf.settings.PrinterConfig import PrinterConfig


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        self.current = PrinterConfig(url="http://printer")
        self.raw = asdict(self.current)
        self.discovery = ConfigDiscovery()

    def validate(self, **changes):
        return validate_config(dict(self.raw, **changes), self.current, self.discovery,
                               url_is_usable=lambda url: url.startswith('http://printer'))

    def test_draft_is_a_value_and_does_not_change_inputs(self):
        before = deepcopy(self.raw)
        value = self.validate(trace_layer=True, recovery_prime_dwell_s=12,
                              follow_mode='unknown', feed_mode='unknown', cache_max_mb='')
        self.assertEqual(self.raw, before)
        self.assertFalse(self.current.trace_layer)
        self.assertTrue(value.trace_layer)
        self.assertEqual(value.recovery_prime_dwell_s, 12)
        self.assertEqual(value.follow_mode, 'exact')
        self.assertEqual(value.cache_max_mb, self.current.cache_max_mb)
        self.assertFalse(value.cache_max_mb_explicit)

    def test_structured_refusals_and_bounds(self):
        cases = [
            ('poll_interval_ms', 249), ('aux_interval_ms', 249), ('console_interval_ms', 60001),
            ('z_tolerance', 0.004), ('ready_retry_interval_s', 0.09), ('cache_max_mb', 15),
            ('poll_interval_ms', 'not a number'), ('filename_translate_input', 'ab'),
            ('recovery_sensor_names', ['bad']),
            ('recovery_sensor_names', ['filament_switch_sensor unknown']),
            ('recovery_nozzle_parameter', 'BAD COMMAND'), ('recovery_bed_parameter', '1'),
            ('recovery_setup_macros', ['unknown']), ('recovery_transparent_macros', 'unknown'),
            ('recovery_saved_state', 'NOT_DISCOVERED'), ('recovery_prepare_macro', 'MISSING'),
            ('recovery_lead_in_mode', 'unknown'), ('recovery_lead_in_moves', 'bad'),
            ('recovery_lead_in_moves', 0), ('recovery_auto_pause', 1), ('recovery_prime_enabled', 1),
            ('recovery_command_timeout_s', 'bad'), ('recovery_command_timeout_s', 9),
            ('recovery_heating_timeout_s', 'bad'), ('recovery_heating_timeout_s', 59),
            ('recovery_status_timeout_s', 'bad'), ('recovery_status_timeout_s', 2),
            ('detection_warning_threshold', 100), ('detection_enabled', 1),
        ]
        for key, low, high in RECOVERY_NUMBER_LIMITS:
            cases.extend([(key, low - 1), (key, high + 1), (key, 'bad'), (key, float('nan'))])
            for value in (low, high):
                with self.subTest(key=key, accepted=value):
                    self.assertEqual(getattr(self.validate(**{key: value}), key), value)
        for key, value in cases:
            with self.subTest(key=key, refused=value), self.assertRaises(ConfigValidationError) as caught:
                self.validate(**{key: value})
            self.assertTrue(caught.exception.field)
            self.assertTrue(str(caught.exception))
        with self.assertRaisesRegex(ConfigValidationError, 'object'):
            validate_config([], self.current, self.discovery, url_is_usable=lambda _: True)

    def test_connection_parser_is_injected_and_disabled_connection_is_allowed(self):
        self.assertFalse(self.validate(enabled=False, url='invalid').enabled)
        with self.assertRaises(ConfigValidationError) as caught:
            self.validate(enabled=True, url='invalid')
        self.assertEqual(caught.exception.field, 'url')
        self.assertEqual(self.validate(enabled=True, url='http://printer/').url, 'http://printer')

    def test_discovered_and_saved_choices_are_accepted_and_deduplicated(self):
        self.discovery = ConfigDiscovery(sensors=('filament_switch_sensor runout',),
                                         saved_states=('RECOVERY',), macros=('START_PRINT', 'NOTE'))
        value = self.validate(recovery_sensor_names=['filament_switch_sensor runout'] * 2,
                              recovery_saved_state='recovery', recovery_prepare_macro='start_print',
                              recovery_setup_macros=['START_PRINT'] * 2, recovery_transparent_macros=['NOTE'])
        self.assertEqual(value.recovery_sensor_names, ['filament_switch_sensor runout'])
        self.assertEqual(value.recovery_setup_macros, ['START_PRINT'])
        with self.assertRaises(ConfigValidationError):
            self.validate(recovery_setup_macros=['NOTE'], recovery_transparent_macros=['NOTE'])
        self.current = value
        self.discovery = ConfigDiscovery()
        self.raw = asdict(value)
        self.assertEqual(self.validate(), value)

    def test_detection_enabling_requires_global_consent_and_camera(self):
        with self.assertRaisesRegex(ConfigValidationError, 'working camera'):
            self.validate(detection_enabled=True)
        self.discovery = ConfigDiscovery(detection_global_enabled=True, detection_camera_ready=True)
        self.assertTrue(self.validate(detection_enabled=True).detection_enabled)
        with self.assertRaises(ConfigValidationError):
            self.validate(detection_enabled=True, camera_disabled=True)
        self.current = replace(self.current, detection_enabled=True)
        self.discovery = ConfigDiscovery()
        self.assertTrue(self.validate(detection_enabled=True).detection_enabled)
