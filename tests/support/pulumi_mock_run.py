"""Run a generated Pulumi program against the real Pulumi library, clouds mocked.

Prints one line per resource type and count, then `OK`. Usage:
    python pulumi_mock_run.py path/to/__main__.py
"""
import collections
import sys

import pulumi

created = []


class _Mocks(pulumi.runtime.Mocks):
    def new_resource(self, args):
        created.append(args.typ)
        outputs = {**args.inputs, "arn": "arn:aws:ec2:us-east-1:123456789012:x/x",
                   "selfLink": f"https://example.invalid/{args.name}", "availabilityZone": "us-east-1a"}
        return [f"{args.name}_id", outputs]

    def call(self, args):
        return {"id": "ami-0123456789abcdef0", "names": ["us-east-1a", "us-east-1b", "us-east-1c"]}


pulumi.runtime.set_mocks(_Mocks(), preview=False)
source = open(sys.argv[1]).read()


@pulumi.runtime.test
def _run():
    exec(compile(source, sys.argv[1], "exec"), {"__name__": "__main__"})


_run()
for kind, count in sorted(collections.Counter(created).items()):
    print(kind, count)
print("OK")
