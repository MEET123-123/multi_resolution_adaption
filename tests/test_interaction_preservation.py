import unittest
from multires_app.export.interaction_preservation import audit_preservation


class PreservationTests(unittest.TestCase):
    source=b'''<Lockscreen><Var name="page" expression="0"/>
    <Group x="0"><Button x="0" y="0" w="1080" h="100">
    <Trigger action="up"><VariableCommand name="page" expression="1"
    condition="gt(#touch_begin_x-#touch_x,200)"/></Trigger></Button></Group></Lockscreen>'''

    def test_geometry_and_coordinate_adapter_preserve_behavior(self):
        candidate=self.source.replace(b'x="0"',b'x="30"').replace(
            b'#touch_begin_x-#touch_x',
            b'((#touch_begin_x-(#mr_a_tx))/(#mr_a_sx))-((#touch_x-(#mr_a_tx))/(#mr_a_sx))')
        report=audit_preservation(self.source,candidate,{'changes':[]})
        self.assertTrue(report['passed']);self.assertEqual(report['coordinateReferencesMapped'],2)

    def test_changed_condition_removed_command_or_reordered_variables_fail(self):
        for candidate in [self.source.replace(b',200)',b',100)'),
                          self.source.replace(b'expression="1"',b'expression="0"'),
                          self.source.replace(b'action="up"',b'action="down"'),
                          self.source.replace(b'<Var name="page" expression="0"/>',b'')]:
            with self.subTest(candidate=candidate),self.assertRaises(ValueError):
                audit_preservation(self.source,candidate,{'changes':[]})

if __name__=='__main__':unittest.main()
