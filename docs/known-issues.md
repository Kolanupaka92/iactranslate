# Known issues

*Author and owner: Sai Kolanupaka*

Problems seen for real, how to recognise them, and what to do. Search this page
for the error text you see. Each entry has an ID so a ticket can cite it.

**Status** is one of:
- **Fixed:** the generator no longer produces it. Code generated before the fix still has it: regenerate, or apply the fix by hand.
- **Workaround:** caused by the account or a tool outside IaCTranslate. Do what the fix says.
- **Open:** known, not fixed yet.

To add an entry: the next free ID in its section, the exact message, the cause in a
sentence or two, the fix, the status, and the ADR or date it came from. A deployment
MOP ([Deploying](deploying.md)) links here by ID.

| ID | Short name | Status |
|---|---|---|
| [KI-001](#ki-001) | Terraform can't use an `aws login` sign-in | Workaround |
| [KI-002](#ki-002) | AWS Free plan refuses the instance type | Workaround |
| [KI-003](#ki-003) | AWS sign-in expired or ended | Workaround |
| [KI-004](#ki-004) | `aws login` request expired | Workaround |
| [KI-010](#ki-010) | Machines behind an AWS load balancer reachable directly | Fixed |
| [KI-011](#ki-011) | AWS HTTPS listener refused: no certificate | Fixed |
| [KI-012](#ki-012) | A newer image replaces every server | Fixed |
| [KI-013](#ki-013) | Adopting a running AWS server replaces it | Fixed |
| [KI-014](#ki-014) | CentOS 7 finds no AWS image | Fixed |
| [KI-015](#ki-015) | SUSE takes a bring-your-own-subscription image | Fixed |
| [KI-016](#ki-016) | Adopting a server deletes its tags and replaces its role | Fixed |
| [KI-017](#ki-017) | Boot disk note misleading (adopted servers, tool minimum) | Fixed |
| [KI-020](#ki-020) | CloudFormation/CDK boot disk small and unencrypted, extra disk | Fixed |
| [KI-021](#ki-021) | CloudFormation drops machines with similar names | Fixed |
| [KI-022](#ki-022) | CloudFormation/CDK data disks left after deleting the stack | By design |
| [KI-023](#ki-023) | CDK deploy fails: not bootstrapped | Workaround |
| [KI-030](#ki-030) | Pulumi: `pip list` fails | Workaround |
| [KI-031](#ki-031) | Pulumi AWS load balancer name refused | Fixed |
| [KI-032](#ki-032) | Pulumi Azure/Google load balancer program won't run | Fixed |
| [KI-033](#ki-033) | Pulumi, Bicep: data disks missing | Fixed |
| [KI-034](#ki-034) | Bicep doesn't compile (BCP079, BCP238) | Fixed |
| [KI-035](#ki-035) | Pulumi Google: every resource name refused | Fixed |
| [KI-036](#ki-036) | Pulumi Google: attached disk has no project | Fixed |
| [KI-037](#ki-037) | Pulumi: no routing or NAT (AWS, Google) | Fixed |
| [KI-040](#ki-040) | Google: `cloud-platform scope is required but not consented` | Workaround |
| [KI-041](#ki-041) | Google internal load balancer: instance in wrong subnetwork | Fixed |
| [KI-042](#ki-042) | Google: changing a machine requires stopping it | Fixed |
| [KI-043](#ki-043) | Google internal load balancer backends never healthy | Fixed |
| [KI-044](#ki-044) | Google data disks detached on a later run | Fixed |
| [KI-045](#ki-045) | Google: a new project's APIs are off | Workaround |
| [KI-050](#ki-050) | Boot disk smaller than the image | Fixed |
| [KI-051](#ki-051) | Oracle: one OCPU counted as one vCPU | Fixed |
| [KI-052](#ki-052) | Oracle: no SUSE image, plan fails | Fixed |
| [KI-053](#ki-053) | Oracle: image lookup picks an Arm or GPU image | Fixed |
| [KI-060](#ki-060) | Machine larger than the largest size, silently shrunk | Fixed |
| [KI-061](#ki-061) | VMware CSV "Provisioned MB" read as no disk | Fixed |
| [KI-070](#ki-070) | Hosted service: database connections run out | Fixed |
| [KI-071](#ki-071) | Hosted service: Google sign-in fails in an embedded browser | Workaround |
| [KI-072](#ki-072) | Hosted service: live database had a public address | Fixed |
| [KI-073](#ki-073) | Restore check: `postgres` can't read the app's tables | Workaround |
| [KI-074](#ki-074) | Pulumi Google: Windows silent after an early restart | Open |
| [KI-075](#ki-075) | Google public load balancer: different address for HTTP and HTTPS | Open |
| [KI-076](#ki-076) | Azure: private machines may have no way out | Open |

---

## Signing in and accounts

### KI-001
**Terraform can't use an `aws login` sign-in.**

- **Message:** `No valid credential sources found` … `failed to refresh cached credentials, no EC2 IMDS role found`
- **Affects:** Terraform and Pulumi on AWS when you signed in with `aws login`.
- **Cause:** the AWS provider can't read `aws login` sessions yet.
- **Fix:** a second profile that asks the AWS CLI for the session, and `AWS_PROFILE=<name>-terraform` for Terraform and Pulumi:
  ```ini
  [profile <name>-terraform]
  credential_process = aws configure export-credentials --profile <name> --format process
  ```
- **Status:** Workaround. The generated README says so (ADR 0094). Seen 26 Nov 2026.

### KI-002
**AWS Free plan refuses the instance type.**

- **Message:** `InvalidParameterCombination: The specified instance type is not eligible for Free Tier.`
- **Affects:** any AWS account on the Free plan. A `run-instances --dry-run` passes anyway: it checks permissions, not the plan.
- **Cause:** the Free plan allows only free-tier instance types (t3.micro, t3.small, c7i-flex.large, m7i-flex.large and a few others).
- **Fix:** upgrade the account to the paid plan (AWS Settings), or test with an inventory that sizes to free-tier types. List them: `aws ec2 describe-instance-types --filters Name=free-tier-eligible,Values=true`.
- **Status:** Workaround (account limit). Seen 26 Nov 2026.

### KI-003
**AWS sign-in expired or ended.**

- **Message:** `Error loading login session token: Unable to load a existing login session … Please reauthenticate with 'aws login'.`
- **Cause:** the session ended (12 hours, or `aws logout`, or ended on AWS's side).
- **Fix:** `aws login --region <region> --profile <name>` again. Nothing deployed is affected.
- **Status:** Workaround. Seen 27 Nov 2026, after about 6 hours.

### KI-004
**`aws login` request expired.**

- **Message:** `The pending authorization to retrieve an SSO token has expired.`
- **Cause:** the browser approval wasn't completed within a few minutes.
- **Fix:** run `aws login` again in a terminal you can see, and approve straight away. If the browser is on another device, add `--remote`.
- **Status:** Workaround. Seen 26 Nov 2026.

## AWS

### KI-010
**Machines behind an AWS load balancer reachable directly from the internet.**

- **Symptom:** a web machine behind the public load balancer has a public IP, and `curl http://<its address>/` answers.
- **Cause:** public subnets assigned every machine a public address at launch.
- **Fix:** subnets assign none; only a stand-alone public machine asks for one; machines behind a public load balancer go in private subnets and out through NAT. Code generated before ADR 0095: set `map_public_ip_on_launch = false` and move those machines (replaces them).
- **Status:** Fixed (ADR 0095). Seen 26 Nov 2026 on a real deployment.

### KI-011
**AWS HTTPS listener refused: no certificate.**

- **Message:** `ValidationError: A certificate must be specified for HTTPS listeners`
- **Cause:** an HTTPS listener was generated with an empty certificate.
- **Fix:** set `acm_certificate_arn` (Terraform), `acmCertificateArn` (Pulumi) or `AcmCertificateArn` (CloudFormation, CDK). Without one the listener isn't created now and the plan warns.
- **Status:** Fixed (ADR 0094). Seen 26 Nov 2026.

### KI-012
**A newer image replaces every server.**

- **Symptom:** a plan weeks after deploying says servers "must be replaced" because of `ami` (AWS), `source_details[0].source_id` (Oracle) or the boot image (Google).
- **Cause:** images are looked up as "newest" on every run.
- **Fix:** the generated code ignores image changes on running servers. Rebuild one on purpose: `terraform apply -replace=<address>`.
- **Status:** Fixed (ADR 0090). Reproduced with `tofu test`; proven on AWS 26 Nov 2026.

### KI-013
**Adopting a running AWS server would replace it.**

- **Symptom:** `import` plus "must be replaced" for the same server.
- **Cause:** the configuration put it in a new subnet with an encrypted boot disk; both replace an instance.
- **Fix:** adopted servers keep their own network, security groups, image, boot disk and address. Never apply a plan that replaces an adopted server.
- **Status:** Fixed (ADR 0091). Proven on AWS 27 Nov 2026: two servers imported, same ids and launch time, nothing replaced.

### KI-014
**CentOS 7 finds no AWS image.**

- **Message:** `Your query returned no results. Please change your search criteria and try again.`
- **Cause:** CentOS 7 is end of life and AWS hides its images.
- **Fix:** CentOS machines get Rocky Linux 9, stated as a substitution. Test the workload on it.
- **Status:** Fixed (ADR 0096).

### KI-015
**SUSE takes a bring-your-own-subscription image.**

- **Symptom:** a SUSE machine boots a `chost-byos` (container host) image that needs your own SUSE registration.
- **Cause:** the image name pattern was too loose.
- **Fix:** the pattern now matches only `suse-sles-15-spN-vYYYYMMDD-hvm-ssd-x86_64`.
- **Status:** Fixed (ADR 0096).

### KI-016
**Adopting a server deletes its tags and replaces its role.**

- **Symptom:** the adoption plan changes `tags` (removing the server's own, such as a cost centre) and sets `iam_instance_profile` to the generated tier role.
- **Cause:** the generated tags and role were applied to adopted servers as to new ones.
- **Fix:** adopted servers keep their own tags and role (`ignore_changes`); the project's default tags are only added. Before ADR 0091's update: add `iam_instance_profile, tags` to the adopted server's `ignore_changes`.
- **Status:** Fixed (ADR 0091). Found 27 Nov 2026 on a real adoption plan.

### KI-017
**Boot disk note misleading.**

- **Symptom:** an adopted server's plan says "Boot disk raised from 8 to 30 GiB" although its disk isn't changed, and prices 30 GB; or a raise to the tool's own 30 GB minimum is put down to the image.
- **Fix:** an adopted server is planned and priced at its own boot disk size with no note; the note now says which minimum applied: the image's or the tool's.
- **Status:** Fixed. Found 27 Nov 2026.

### KI-020
**CloudFormation/CDK: boot disk small and unencrypted, plus an extra disk.**

- **Symptom:** more volumes than planned; the boot disk is the image's default (8 GB for Ubuntu) and unencrypted; an empty encrypted disk at `/dev/xvda` is unused.
- **Cause:** the boot disk was mapped to `/dev/xvda`; Ubuntu, Red Hat, SUSE and Windows images boot from `/dev/sda1`.
- **Fix:** the mapping follows the image. Code generated before ADR 0096: change the first `DeviceName` to `/dev/sda1` for those images (replaces the instances).
- **Status:** Fixed (ADR 0096). Seen 27 Nov 2026 on a real deployment.

### KI-021
**CloudFormation drops machines with similar names.**

- **Symptom:** fewer instances in the template than in the plan; CDK says `There is already a Construct with name 'Instance…'`.
- **Cause:** names differing only in punctuation ("uat cache 01", "uat-cache-01") got the same id.
- **Fix:** every machine gets a distinct id; the first keeps the plain one.
- **Status:** Fixed (ADR 0093).

### KI-022
**CloudFormation/CDK data disks left after deleting the stack.**

- **Symptom:** `available` volumes, still billed, after `delete-stack` or `cdk destroy`.
- **Cause:** by design, data disks outlive their instance so data is never lost with a server.
- **Fix:** list them before deleting the stack (MOP-AWS-CFN step 6) and delete them afterwards.
- **Status:** By design (ADR 0093, 0096).

### KI-023
**CDK deploy fails: not bootstrapped.**

- **Symptom:** a deploy to an account or region never bootstrapped fails: CDK's templates check for its bootstrap stack.
- **Fix:** `npx aws-cdk@<version> bootstrap aws://<account>/<region>` once per account and region. To remove it later, delete the `CDKToolkit` stack and empty and delete its bucket, which AWS keeps.
- **Status:** Workaround (how CDK works).

## Pulumi and Bicep

### KI-030
**Pulumi: `pip list` fails.**

- **Message:** `failed to discover package requirements: calling … -m pip list --format json: exit status 1`
- **Cause:** the Python environment has no `pip` (for example one made by `uv venv`).
- **Fix:** `python -m ensurepip` or `uv pip install pip` in that environment.
- **Status:** Workaround. Seen 27 Nov 2026.

### KI-031
**Pulumi AWS load balancer or target group name refused.**

- **Message:** `only alphanumeric characters and hyphens allowed in "name"`
- **Cause:** Pulumi named them after the label, which has an underscore.
- **Fix:** explicit names, as Terraform uses.
- **Status:** Fixed (ADR 0096). Seen 27 Nov 2026.

### KI-032
**Pulumi Azure or Google program won't run with a load balancer.**

- **Message:** `BackendAddressPool._internal_init() got an unexpected keyword argument 'resource_group_name'`, or `Unexpected type. Expected 'list' got '<class 'str'>'` on `health_checks`.
- **Fix:** corrected arguments.
- **Status:** Fixed (ADR 0090). CI now runs every Pulumi program (ADR 0093).

### KI-033
**Pulumi or Bicep: data disks missing.**

- **Symptom:** machines come up without their data disks.
- **Status:** Fixed (ADR 0090). Next run of an older project proposes the disks, empty.

### KI-034
**Bicep doesn't compile.**

- **Message:** `BCP079: This expression is referencing its own declaration` and `BCP238: Unexpected new line character after a comma`
- **Cause:** the load balancer referred to its own parts through itself; array items separated by commas across lines.
- **Fix:** `resourceId(…)` references; new-line separated items.
- **Status:** Fixed (ADR 0093). `bicep build` runs in CI.

### KI-035
**Pulumi Google: every resource name refused.**

- **Message:** `"name" ("public_0-98aea84") doesn't match regexp "^(?:[a-z](?:[-a-z0-9]{0,61}[a-z0-9])?)$"`
- **Cause:** Pulumi names a resource after its label, which had underscores.
- **Fix:** lowercase, hyphenated labels; machines get Terraform's names.
- **Status:** Fixed (ADR 0097). Seen 27 Nov 2026.

### KI-036
**Pulumi Google: attached disk has no project.**

- **Message:** `Failed to retrieve project, pid: , err: project: required field is not set`
- **Status:** Fixed (ADR 0097).

### KI-037
**Pulumi: no routing or NAT.**

- **Symptom:** on AWS the public load balancer can't be reached and private machines can't reach the internet; on Google private machines can't.
- **Cause:** Pulumi didn't build Terraform's route tables, NAT gateway, or Google's router and NAT.
- **Fix:** it does now. Proven on Google and on AWS, 27 Nov 2026: on AWS the public load balancer answered from the internet and a private machine reached the internet through NAT.
- **Status:** Fixed (ADR 0097).

## Google Cloud

### KI-040
**`cloud-platform scope is required but not consented`.**

- **Cause:** the Google Cloud box on the consent page wasn't ticked during `gcloud auth application-default login`.
- **Fix:** run it again and tick it.
- **Status:** Workaround. Seen 24 Nov 2026.

### KI-041
**Google internal load balancer: instance in the wrong subnetwork.**

- **Message:** `Resource '…/instances/…' is expected to be in the subnetwork '…' but is in the subnetwork '…'`, `wrongSubnetwork`
- **Cause:** machines behind an internal load balancer were spread across subnets; Google needs them in the forwarding rule's.
- **Status:** Fixed (ADR 0092). Seen 26 Nov 2026 on a real deployment.

### KI-042
**Google: changing a machine requires stopping it.**

- **Message:** `Changing the machine_type … on a started instance requires stopping it. To acknowledge this, please set allow_stopping_for_update = true`
- **Cause:** right-sizing or moving a deployed machine; the code didn't allow a stop.
- **Fix:** `allow_stopping_for_update = true`, generated now. The machine restarts.
- **Status:** Fixed (ADR 0092).

### KI-043
**Google internal load balancer backends never healthy.**

- **Cause:** the firewall didn't admit Google's health probes from `35.191.0.0/16`.
- **Status:** Fixed (ADR 0089). Proven healthy 26 Nov 2026.

### KI-044
**Google data disks detached on a later run.**

- **Cause:** a machine and its separately attached disks "fight for control" without `ignore_changes = [attached_disk]`.
- **Status:** Fixed (ADR 0089). Proven with no drift 26 Nov 2026.

### KI-045
**Google: a new project's APIs are off.**

- **Symptom:** the first apply in a new project fails because the Compute Engine or IAM API is disabled.
- **Fix:** `gcloud services enable compute.googleapis.com iam.googleapis.com --project <project>`.
- **Status:** Workaround. The README says so.

## Any cloud

### KI-050
**Boot disk smaller than the image.**

- **Symptom:** the apply is refused for a boot disk below the image's size: Google Windows images are 50 GB, Azure's usually 127, and Oracle's minimum is 50. Found by reading the clouds' documentation before deploying, so the exact messages weren't seen.
- **Fix:** each cloud states its minimum and the boot disk is never below it (Google Windows 50 GB, Azure Windows 127 GB, Oracle 50 GB). The plan says when it raised one.
- **Status:** Fixed (ADR 0089). Proven on Google 26 Nov 2026.

### KI-051
**Oracle: one OCPU counted as one vCPU.**

- **Symptom:** Oracle machines had twice the CPU they needed; Windows licensing half the real figure.
- **Status:** Fixed (ADR 0088). Regenerating halves an Oracle machine's OCPUs.

### KI-052
**Oracle: no SUSE image, the plan fails.**

- **Symptom:** `images[0]` index error on a SUSE machine's image lookup.
- **Fix:** SUSE gets Oracle Linux 9, stated as a substitution.
- **Status:** Fixed (ADR 0093).

### KI-053
**Oracle: image lookup picks an Arm or GPU image.**

- **Status:** Fixed (ADR 0090): the lookup asks for images that fit the shape.

### KI-060
**Machine larger than any size in the catalog, silently shrunk.**

- **Symptom:** a 64 vCPU / 512 GB machine planned as 48/192 or smaller with nothing said.
- **Fix:** larger sizes in every catalog; anything still too big is marked "DOES NOT FIT" everywhere and that cloud isn't recommended.
- **Status:** Fixed (ADR 0086).

### KI-061
**VMware CSV "Provisioned MB" read as no disk.**

- **Symptom:** every machine gets a 30 GB boot disk regardless of its size.
- **Status:** Fixed (ADR 0089). A bare "Provisioned" header is still not read: its unit is unknown.

## The hosted service

### KI-070
**Database connections run out.**

- **Symptom:** API requests fail with database connection errors under ordinary load.
- **Cause:** each store opened its own connection pool against a `db-f1-micro` database, which allows few connections.
- **Fix:** one shared pool.
- **Status:** Fixed (November 2026).

### KI-071
**Google sign-in fails in an embedded browser.**

- **Symptom:** a passkey prompt that can't complete.
- **Fix:** sign in from a normal browser such as Chrome.
- **Status:** Workaround. Seen 24 Nov 2026.

### KI-072
**The live database had a public address.**

- **Symptom:** `gcloud sql instances describe iactranslate-db` shows `ipv4Enabled: true` and a PRIMARY address, though ADR 0068 says it was removed.
- **Risk:** no networks are authorized and connections must be encrypted, so only Google's signed-in connectors can reach it. But it isn't what the record says.
- **Cause:** ADR 0068's last step (removing the public address) was never run. The audit log shows the private address added on 15 Sep 2026 and no change to the public one until this fix.
- **Fix:** `gcloud sql instances patch iactranslate-db --no-assign-ip`, after confirming nothing uses it: the API has no Auth Proxy and reaches the database over Direct VPC egress. Took 76 seconds; no errors in the API or database logs; the API kept reaching the database.
- **Check it stays fixed:** `gcloud sql instances describe iactranslate-db --format='value(settings.ipConfiguration.ipv4Enabled)'` prints `False`.
- **Status:** Fixed 27 Nov 2026.

### KI-073
**Checking a restored database: the `postgres` user can't read the app's tables.**

- **Symptom:** the tables are listed but every query fails with a permission error; or `information_schema.tables` shows none, because it only lists tables you can access.
- **Cause:** on Cloud SQL, `postgres` isn't a superuser, and the tables belong to `iacapp`.
- **Fix:** set a new password for `iacapp` on the **restored** instance only and connect as `iacapp` (MOP 8 step 4).
- **Status:** Workaround. Seen 27 Nov 2026, rehearsing MOP 8.

### KI-074
**Pulumi Google: Windows silent after an early restart.**

- **Symptom:** both Windows machines booted and started Google's agent, were restarted 90 seconds after creation for a test, and then printed nothing past the Windows Boot Manager, through a second restart.
- **Likely causes:** restarting during Windows' first-boot setup; or no service account (Pulumi's machines have none, Terraform's do), which the agent logged as missing credentials.
- **Workaround:** don't restart a new Windows machine until "Windows is Ready to use" is on its serial console.
- **Status:** Open. Seen 27 Nov 2026.

### KI-075
**Google public load balancer: different address for HTTP and HTTPS.**

- **Symptom:** port 80 and port 443 have different IP addresses, so one web address can't point at both.
- **Cause:** each forwarding rule takes its own ephemeral address; they should share one reserved address.
- **Status:** Open. Seen 27 Nov 2026, in Terraform and Pulumi.

### KI-076
**Azure: private machines may have no way out to the internet.**

- **Symptom (expected, not seen):** private Azure machines can't install updates.
- **Cause:** the Azure output has no NAT gateway, and as far as I know Azure stopped giving new networks default outbound access in 2025. Not verified: Azure hasn't been deployed for real.
- **Status:** Open. Check on the first Azure deployment.

