"""Visual fan phase, telemetry precedence, bad inputs and analytic axes."""
import math
import unittest
import numpy as np
from mpf.geometry.ToolheadRotors import finite, rotors, speed, RotorMotion, rotation, body_axis
from mpf.geometry.ToolheadFanReadings import FanReadings
from mpf.geometry.ToolheadGeometry import default_mesh
from mpf.geometry.ToolheadRotorAxis import rotation_axis


class RotorTests(unittest.TestCase):
    def row(self, **kwargs):
        return dict(body=0, centre=[0,0,0], axis=[0,0,1], rpm=3000., direction=1, fan='', blur=True, **kwargs)

    def test_invalid_rotor_config_is_rejected_and_capacity_is_bounded(self):
        good=self.row()
        self.assertEqual(len(rotors([good]*12)),1)
        for key,value in [('body',True),('body',4096),('axis',[0,0,0]),('axis',[0,0,math.nan]),('centre',[0]),('rpm',True),('rpm',math.inf),('fan','heater_fan\nX'),('direction',0)]:
            with self.subTest(key=key,value=value): self.assertEqual(rotors([dict(good,**{key:value})]),[])
        self.assertEqual(rotors('bad'),[])
        self.assertEqual(rotors([None]),[])
        self.assertEqual(rotors([dict(good,body=9)],default_mesh()),[])
        self.assertEqual(len(rotors([dict(good,body=i) for i in range(20)])),8)

    def test_huge_integers_and_invalid_observation_times_fail_closed(self):
        self.assertIsNone(finite(10**999, 0, math.inf))
        self.assertIsNone(finite("1", 0, 1))
        readings=FanReadings()
        for stamp in (None, "1", 10**999, math.nan, math.inf, -1):
            readings.accept({'fan': {'rpm': 100}}, stamp, full=True)
            self.assertEqual(readings.rows, {})
        row=dict(self.row(), fan='fan')
        self.assertEqual(speed(row, {'fan': dict(available=True,rpm=10**999,speed=.5)})[0], 1500.)

    def test_measured_zero_wins_over_power_and_invalid_rpm_uses_estimate(self):
        row=dict(self.row(),fan='fan')
        self.assertEqual(speed(row,{'fan':dict(rpm=0.,speed=1.,available=True)}),(0.,'Measured RPM'))
        self.assertEqual(speed(row,{'fan':dict(rpm=123.,speed=1.,available=True)})[0],123.)
        self.assertEqual(speed(row,{'fan':dict(rpm=math.nan,speed=.5,available=True)}),(1500.,'Estimated from power'))
        self.assertEqual(speed(row,{'fan':dict(rpm=100.,speed=1.,available=False)})[0],0.)
        self.assertEqual(speed(row,{})[0],0.)
        self.assertEqual(speed(row,{'fan':dict(speed=2.,available=True)})[0],0.)

    def test_multiple_rotors_share_one_fan_without_merging_body_settings(self):
        rows=rotors([dict(self.row(),body=0,fan='fan_generic cooling',rpm=6000.,direction=1),
                     dict(self.row(),body=1,fan='fan_generic cooling',rpm=4000.,direction=-1)])
        self.assertEqual(len(rows),2)
        readings={'fan_generic cooling':dict(available=True,speed=.5)}
        self.assertEqual([speed(row,readings)[0] for row in rows],[3000.,2000.])
        readings['fan_generic cooling']['rpm']=1200.
        self.assertEqual([speed(row,readings)[0] for row in rows],[1200.,1200.])
        clock=[1.]
        motion=RotorMotion(clock=lambda:clock[0])
        motion.configure(rows,readings)
        clock[0]+=.01
        samples=motion.sample()
        self.assertEqual([row[0]['body'] for row in samples],[0,1])
        self.assertAlmostEqual((samples[0][1]+samples[1][1])%math.tau,0.,places=6)
        motion.configure(rows,{'fan_generic cooling':dict(available=True,speed=1.,rpm=0.)})
        self.assertFalse(motion.moving)
        motion.configure(rows,{})
        self.assertFalse(motion.moving)

    def test_phase_uses_old_speed_before_switch_and_hidden_resume_is_bounded(self):
        clock=[1.]; motion=RotorMotion(clock=lambda:clock[0])
        motion.configure(rotors([dict(self.row(),rpm=60.)]),{})
        clock[0]+=.05
        self.assertAlmostEqual(motion.sample()[0][1],math.pi*.1)
        motion.configure(rotors([dict(self.row(),rpm=0.)]),{})
        clock[0]+=500
        self.assertAlmostEqual(motion.sample()[0][1],math.pi*.1)
        self.assertFalse(motion.moving)
        motion.configure(rotors([dict(self.row(),direction=-1,rpm=30000.)]),{})
        clock[0]+=.05
        sample=motion.sample()[0]; self.assertTrue(0<=sample[1]<math.tau)
        self.assertEqual(sample[2],math.pi/3)
        motion.configure(rotors([dict(self.row(),blur=False)]),{})
        self.assertEqual(motion.sample()[0][2],0.)

    def test_axis_consensus_and_projection_avoid_guessing_ambiguous_cylinders(self):
        centre,axis=rotation_axis([([0,0,0],[0,0,-1]),([0,0,3],[0,0,1])],[-1,-1,0],[1,1,4])
        self.assertEqual(centre,[0,0,2]); self.assertEqual(axis,[0,0,1])
        for values in ([],[([0,0,0],[0,0,0])], [([0,0,0],[0,0,1]),([1,0,0],[0,0,1])], [([0,0,0],[0,0,1]),([0,0,0],[1,0,0])], [([0,0,0],[0,0,1])]*65):
            self.assertEqual(rotation_axis(values,[-1,-1,0],[1,1,4]),(None,None))
        centre,axis,proven=body_axis(default_mesh(),0)
        self.assertFalse(proven); self.assertEqual(len(centre),3); self.assertAlmostEqual(np.linalg.norm(axis),1)
        np.testing.assert_allclose(rotation([1,2,3],[0,0,1],math.pi/2) @ [2,2,3,1],[1,3,3,1],atol=1e-6)

    def test_full_observation_drops_old_rpm_and_missing_fans_while_fragments_retain_zero(self):
        readings=FanReadings()
        readings.accept({'fan':{'rpm':0.,'speed':1.},'output_pin foo':{'speed':1.}},1,full=True)
        readings.accept({'fan':{'speed':.5}},2)
        self.assertEqual(readings.values(3,connected=True)['fan']['rpm'],0.)
        readings.accept({'fan':{'speed':.2}},4,full=True)
        self.assertIsNone(readings.values(5,connected=True)['fan']['rpm'])
        self.assertFalse(readings.values(15,connected=True)['fan']['available'])
        self.assertTrue(readings.values(15,connected=True,streaming=True)['fan']['available'])
        self.assertFalse(readings.values(5,connected=False,streaming=True)['fan']['available'])
        readings.accept({},6,full=True); self.assertEqual(readings.rows,{})
