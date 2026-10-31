import unittest
from steambridge.mapper import Mapper, deadzone, pad_directions
from steambridge.model import Frame, Pad, Profile
from steambridge.updater import version_tuple
class Tests(unittest.TestCase):
    def test_sticks(self):
        self.assertEqual(deadzone(.01,.02,.15),(0,0))
    def test_pad(self):
        self.assertEqual(pad_directions(Pad(True,.95,.1),.15),{"dpad_right","dpad_up"})
    def test_bindings(self):
        out=Mapper().map(Frame(buttons={"a","right_grip"}),Profile())
        self.assertIn("a",out.xbox)
        self.assertIn("space",out.keys)
    def test_mouse_touch(self):
        m=Mapper(); p=Profile(gyro_mode="off",trackball=False)
        self.assertEqual(m.map(Frame(right_pad=Pad(True,.7,.5)),p).mouse_dx,0)
        self.assertGreater(m.map(Frame(right_pad=Pad(True,.8,.5)),p).mouse_dx,0)
    def test_version(self):
        self.assertGreater(version_tuple("v2.0.0"),version_tuple("1.9.10"))
    def test_validation(self):
        with self.assertRaises(ValueError):
            Profile.from_dict({"gyro_mode":"bad"})
if __name__=="__main__":
    unittest.main()
