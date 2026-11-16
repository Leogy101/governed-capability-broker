import contextlib
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from gcb.cli import main, parse_json
from gcb.demo import run_demo, seed_demo
from gcb.gate0 import builtin_registry
from gcb.inventory import Inventory
from gcb.validation import ValidationError


def command(*arguments):
    stdout,stderr=io.StringIO(),io.StringIO()
    with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
        try:
            status=main(list(arguments))
        except SystemExit as exc:
            status=exc.code
    return status,stdout.getvalue(),stderr.getvalue()


class CliIntegrationTests(unittest.TestCase):
    def test_offline_demo_and_doctor_without_network(self):
        with patch.object(socket.socket,"connect",side_effect=AssertionError("network forbidden")), \
             patch.object(socket,"getaddrinfo",side_effect=AssertionError("DNS forbidden")):
            status,out,error=command("demo")
            self.assertEqual(status,0,error)
            self.assertEqual(json.loads(out)["mode"],"offline_synthetic")
            status,out,error=command("doctor")
            self.assertEqual(status,0,error)
            self.assertFalse(json.loads(out)["network_probes"])

    def test_durable_demo_inspection_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            database=str(Path(tmp)/"inventory.db")
            self.assertEqual(command("--db",database,"init")[0],0)
            self.assertEqual(command("--db",database,"demo")[0],0)
            self.assertEqual(command("--db",database,"demo")[0],0)
            status,out,_=command("--db",database,"models")
            self.assertEqual(status,0)
            self.assertEqual(json.loads(out)[0]["id"],"fixture")
            status,out,_=command("--db",database,"research","show","--model","fixture")
            self.assertEqual(status,0)
            self.assertEqual(len(json.loads(out)["facts"]),5)
            status,out,_=command("--db",database,"route","--task",'{"require_tools":true}')
            self.assertEqual(status,3)
            self.assertEqual(json.loads(out)["status"],"no_eligible")

    def test_manual_identity_and_research_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            database=str(Path(tmp)/"inventory.db")
            prefix=("--db",database)
            self.assertEqual(command(*prefix,"init")[0],0)
            records=[("provider",{"id":"p","name":"Fixture"}),
                     ("backend",{"id":"b","provider_id":"p","api_family":"fixture","revision":"1"}),
                     ("model",{"id":"m","backend_id":"b","native_id":"fixture"})]
            for kind,record in records:
                self.assertEqual(command(*prefix,kind,"add","--record",json.dumps(record))[0],0)
            status,out,error=command(*prefix,"research","add","--model","m","--property","max_output","--value","512",
                                     "--verification","documented","--source-url","https://example.org/spec",
                                     "--source-title","Fixture specification","--checked-at","2026-11-14T00:00:00Z")
            self.assertEqual(status,0,error)
            self.assertEqual(len(json.loads(out)["fact_id"]),64)

    def test_gate_command_does_not_need_inventory(self):
        status,out,error=command("gate","--operation","math.calculate","--input",'{"operator":"divide","operands":[2,3]}')
        self.assertEqual(status,0,error)
        self.assertEqual(json.loads(out)["value"],{"numerator":2,"denominator":3})

    def test_absent_inventory_is_not_silently_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            database=Path(tmp)/"missing.db"
            self.assertEqual(command("--db",str(database),"models")[0],2)
            self.assertFalse(database.exists())

    def test_invalid_json_fields_and_argument_errors_do_not_echo_input(self):
        for arguments in (("gate","--operation","math.calculate","--input","SECRET_CANARY"),
                          ("demo","--SECRET_CANARY"),
                          ("provider","add","--record",'{"api_key":"SECRET_CANARY"}')):
            status,out,error=command(*arguments)
            self.assertEqual(status,2)
            self.assertNotIn("SECRET_CANARY",out+error)

    def test_json_duplicate_nan_depth_and_limits(self):
        for value in ('{"x":1,"x":2}', '{"x":NaN}', '['*1000+']'*1000, '"'+'x'*65537+'"'):
            with self.subTest(value=value[:20]),self.assertRaises(ValidationError):
                parse_json(value)


class PrivacyTests(unittest.TestCase):
    def test_environment_credentials_and_gate_inputs_never_persist(self):
        secret="CREDENTIAL_CANARY_G1"
        prompt="PRIVATE_PROMPT_CANARY_G1"
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{"OPENAI_API_KEY":secret,"PRIVATE_PROMPT":prompt}):
            path=Path(tmp)/"inventory.db"
            with Inventory(path) as inventory:
                seed_demo(inventory)
                before=tuple(inventory.db.iterdump())
                result=builtin_registry().run("data.select",{"records":[{"value":prompt}],"fields":["value"]})
                self.assertEqual(result.value,[{"value":prompt}])
                run_demo(inventory)
                after=tuple(inventory.db.iterdump())
                self.assertEqual(before,after)
                serialized="\n".join(after)
                self.assertNotIn(secret,serialized)
                self.assertNotIn(prompt,serialized)
            self.assertNotIn(secret.encode(),path.read_bytes())
            self.assertNotIn(prompt.encode(),path.read_bytes())

    def test_unprofiled_models_stay_in_inventory_without_universal_grade(self):
        # G1 has no universal quality field or deletion policy; absence of profiles is explicit.
        with Inventory() as inventory:
            seed_demo(inventory)
            self.assertEqual(len(inventory.models()),1)
            self.assertEqual(len(inventory.configurations()),2)
            self.assertNotIn("score",inventory.models()[0].__dict__)


if __name__ == "__main__":
    unittest.main()
