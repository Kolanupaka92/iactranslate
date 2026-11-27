# 0091. Adopting a server never moves or replaces it

**Status:** Accepted
**Author:** Sai Kolanupaka
**Date:** 2026-11-26

## Context

An AWS inventory with instance ids produces `import` blocks, so Terraform takes
over the running servers instead of creating copies. The same configuration then
gave each server a subnet and security group in the newly generated network, and
an encrypted boot disk.

The AWS provider replaces an instance when its subnet or its boot disk's
encryption changes (read from the provider's source, v5.100.0). Security groups
from another network can't be attached at all. So adopting a running server
planned to destroy it and build a new one, or failed. Reproduced with
`tofu test`: deploy, then change the new network's subnets and security group;
the adopted server followed them.

Three more problems were found on the way:

- Any id was accepted. An Azure VM id would have produced an AWS `import` block
  that fails, and on other clouds an id was silently ignored.
- An adopted server could be put behind a generated load balancer, which can't
  reach a server in another network.
- Switching to the `for_each` layout would have moved an adopted server to an
  address that doesn't exist, which destroys it.

## Decision

- **Only an EC2 instance id is adopted**, and only on AWS. Anything else plans a new
  server, and the sizing reason says so.
- **An adopted server keeps its own network, security groups, image and boot disk.**
  Terraform ignores changes to them (`ignore_changes`). In the `for_each` layout,
  adopted servers are a resource of their own (`aws_instance.adopted`) because
  `ignore_changes` can't differ between rows of one resource.
- **A different instance type is applied in place.** AWS stops and starts the server
  to do it. The sizing reason and `imports.tf` say so.
- **No new load balancer fronts it, and its data disks aren't created again.**
- **Pulumi** imports with every input that may differ ignored, because Pulumi refuses
  an import whose inputs don't match. Resizing is a later, deliberate change.
- The layout migration file moves an adopted server to `adopted[...]`.

## Consequences

- Adoption brings servers under management where they are. The generated network
  is for new servers; moving an adopted one into it is a replacement, to be done
  deliberately with `terraform apply -replace=<address>`.
- The test fails without the fix and passes with it, in both layouts. A mocked
  provider can't import (OpenTofu crashes), so the test deploys the server first.
- **Proven on AWS, 27 November 2026:** two running servers in the default network,
  with public addresses and unencrypted 8 GB disks, were imported: "2 to import, 0
  to destroy". They kept their instance ids and launch time, network, address,
  security group, disks and tags; a second plan proposed nothing; switching to the
  `for_each` layout moved them to `adopted[...]` and changed nothing.
- That run found that adoption still replaced the server's own tags and role. An
  adopted server now keeps both (`iam_instance_profile`, `tags` ignored), and is
  planned and priced at its own boot disk size.
- Not covered: an Arm (Graviton) instance adopted and resized to an x86 type is
  replaced, because the provider replaces on a change of architecture. Every type in
  the catalog is x86.
