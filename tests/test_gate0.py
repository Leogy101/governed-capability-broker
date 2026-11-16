import unittest

from gcb.gate0 import Gate0Registry, GateContext, HandlerSpec, builtin_registry
from gcb.inventory import Inventory
from gcb.demo import seed_demo
from gcb.validation import ValidationError, object_fields, text


class Gate0Tests(unittest.TestCase):
    def setUp(self):
        self.registry = builtin_registry()

    def test_all_arithmetic_operations_use_exact_rationals(self):
        cases = [("add",[1,2,3],6,1), ("subtract",[5,2],3,1), ("multiply",[6,7],42,1), ("divide",[2,3],2,3)]
        for operation, operands, numerator, denominator in cases:
            result = self.registry.run("math.calculate", {"operator":operation,"operands":operands})
            self.assertEqual(result.status, "handled")
            self.assertEqual(result.value, {"numerator":numerator,"denominator":denominator})
            self.assertEqual(len(result.result_sha256),64)

    def test_arithmetic_bounds_bool_floats_and_eval_rejected(self):
        for arguments in ({"operator":"divide","operands":[1,0]}, {"operator":"add","operands":[True,2]},
                          {"operator":"add","operands":[1.1,2]}, {"operator":"add","operands":[10**10,2]},
                          {"operator":"add","operands":[1]*33}, {"operator":"eval","operands":[1,2]},
                          {"expression":"__import__('os').system('echo unsafe')"}):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.registry.run("math.calculate", arguments).status, "denied")

    def test_select_projects_only_requested_fields_and_preserves_input(self):
        arguments = {"records":[{"id":1,"secret":"not selected"}],"fields":["id"]}
        result = self.registry.run("data.select", arguments)
        self.assertEqual(result.value,[{"id":1}])
        self.assertEqual(arguments["records"][0]["secret"],"not selected")

    def test_transform_malformed_and_oversized_inputs_denied(self):
        for arguments in ({"records":[{}],"fields":["missing"]}, {"records":[],"fields":["id","id"]},
                          {"records":[{}]*257,"fields":["id"]}, {"records":[],"fields":[[]]}):
            self.assertEqual(self.registry.run("data.select", arguments).status,"denied")

    def test_metadata_requires_out_of_band_permission(self):
        with Inventory() as inventory:
            seed_demo(inventory)
            self.assertEqual(self.registry.run("inventory.describe", {"model_id":"fixture"}, GateContext(inventory=inventory)).status,"denied")
            allowed = GateContext(frozenset({"inventory.read"}),inventory)
            self.assertEqual(self.registry.run("inventory.describe", {"model_id":"fixture"}, allowed).value["native_id"],"fixture-model")
            self.assertEqual(self.registry.run("inventory.describe", {"model_id":"fixture","approved":True},allowed).status,"denied")

    def test_unknown_operation_declines(self):
        self.assertEqual(self.registry.run("shell.run", {"command":"ignored"}).status,"declined")
        self.assertEqual(self.registry.run(None, {}).reason,"no_explicit_operation")

    def test_explicit_developer_registration_and_permissions(self):
        def validate(data):
            object_fields(data,{"text"})
            return text(data["text"],"text",128)
        handler = HandlerSpec("example.upper","1",validate,lambda value,ctx:value.upper(),frozenset({"example.use"}))
        with self.assertRaises(ValidationError):
            self.registry.register(handler)
        self.registry.register(handler,approved=True)
        self.assertEqual(self.registry.run("example.upper",{"text":"abc"}).status,"denied")
        allowed=GateContext(frozenset({"example.use"}))
        self.assertEqual(self.registry.run("example.upper",{"text":"abc"},allowed).value,"ABC")
        with self.assertRaises(ValidationError):
            self.registry.register(handler,approved=True)

    def test_handler_failure_is_denied_without_leaking_exception(self):
        def fail(value,ctx):
            raise RuntimeError("SECRET_CANARY")
        self.registry.register(HandlerSpec("example.fail","1",lambda value:value,fail),approved=True)
        result=self.registry.run("example.fail",{})
        self.assertEqual(result.status,"denied")
        self.assertNotIn("SECRET_CANARY",repr(result))

    def test_validator_failure_is_denied_before_routing_without_leaking_exception(self):
        from gcb.models import RoutingPolicy, TaskRequirements
        from gcb.routing import preview_route
        def fail(_value):
            raise RuntimeError("VALIDATOR_CANARY")
        self.registry.register(HandlerSpec("example.bad-validator","1",fail,lambda value,ctx:value),approved=True)
        with Inventory() as inventory:
            seed_demo(inventory)
            result = preview_route(inventory,TaskRequirements(),RoutingPolicy(),operation="example.bad-validator",
                                   arguments={},gate_registry=self.registry)
        self.assertEqual(result.status,"denied")
        self.assertFalse(result.candidates)
        self.assertNotIn("VALIDATOR_CANARY",repr(result))

    def test_invalid_result_and_input_are_bounded(self):
        self.registry.register(HandlerSpec("example.large","1",lambda value:value,lambda value,ctx:"x"*65537),approved=True)
        self.assertEqual(self.registry.run("example.large",{}).status,"denied")
        self.assertEqual(self.registry.run("data.select",{"records":[{"id":"x"*65537}],"fields":["id"]}).status,"denied")


if __name__ == "__main__":
    unittest.main()
