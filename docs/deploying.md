# Deploying the generated code

*Author and owner: Sai Kolanupaka*

Step-by-step procedures (MOPs) for deploying what IaCTranslate generates, one per
cloud and tool. Each is named, so a ticket or a runbook can point at it: "follow
MOP-AWS-TF".

Each MOP says whether it has been **run for real**: applied to a real account,
checked and destroyed. The others are written from the generated code and its
tests, and the first real run is the real check. When something fails, look it up
in [Known issues](known-issues.md) by the error text.

| MOP | Cloud | Tool | Run for real |
|---|---|---|---|
| [MOP-AWS-TF](#mop-aws-tf--aws-with-terraform) | AWS | Terraform / OpenTofu | Yes, 26 Nov 2026 (ADR 0095) |
| [MOP-AWS-CFN](#mop-aws-cfn--aws-with-cloudformation) | AWS | CloudFormation | Yes, 27 Nov 2026 (ADR 0096) |
| [MOP-AWS-CDK](#mop-aws-cdk--aws-with-cdk) | AWS | CDK (Python) | Yes, 27 Nov 2026 (ADR 0096) |
| [MOP-AWS-PULUMI](#mop-aws-pulumi--aws-with-pulumi) | AWS | Pulumi (Python) | Yes, 27 Nov 2026 (ADR 0096) |
| [MOP-AWS-ADOPT](#mop-aws-adopt--adopt-running-aws-servers) | AWS | Terraform, adopting running servers | Yes, 27 Nov 2026 (ADR 0091) |
| [MOP-GCP-TF](#mop-gcp-tf--google-cloud-with-terraform) | Google Cloud | Terraform / OpenTofu | Yes, 24 and 26 Nov 2026 (ADR 0084, 0092) |
| [MOP-GCP-PULUMI](#mop-gcp-pulumi--google-cloud-with-pulumi) | Google Cloud | Pulumi (Python) | Yes, 27 Nov 2026 (ADR 0097); Windows unresolved |
| [MOP-AZ-TF](#mop-az-tf--azure-with-terraform) | Azure | Terraform / OpenTofu | No |
| [MOP-AZ-BICEP](#mop-az-bicep--azure-with-bicep) | Azure | Bicep | No (compiles in CI) |
| [MOP-AZ-PULUMI](#mop-az-pulumi--azure-with-pulumi) | Azure | Pulumi (Python) | No |
| [MOP-OCI-TF](#mop-oci-tf--oracle-cloud-with-terraform) | Oracle Cloud | Terraform / OpenTofu | No |
| [MOP-DO-TF](#mop-do-tf--digitalocean-with-terraform) | DigitalOcean | Terraform / OpenTofu | No |
| [MOP-NTNX-TF](#mop-ntnx-tf--nutanix-ahv-with-terraform) | Nutanix AHV | Terraform / OpenTofu | No |
| [MOP-PVE-TF](#mop-pve-tf--proxmox-ve-with-terraform) | Proxmox VE | Terraform / OpenTofu | No |
| [MOP-K8S](#mop-k8s--kubernetes-with-kubevirt) | Kubernetes (KubeVirt) | kubectl | No |

## Before any deployment

These apply to every MOP below.

1. **Read the plan, not just the summary.** Open `README.md`, `decisions.json` and
   `documentation/migration-summary.md` in the generated folder. Anything marked
   "DOES NOT FIT", "OS substituted" or "OS FAMILY CHANGED" needs a decision first.
2. **Use remote state for anything you keep.** Generate with `--state-backend` or
   follow the README's "Terraform state" section. Local state is for trials.
3. **Plan, save the plan, apply that plan.** `terraform plan -out=x.tfplan`, read
   it, `terraform apply x.tfplan`. Never `apply` without reading what it creates.
4. **After applying, plan again. It must say "No changes".** Anything else is a
   defect: record it in [Known issues](known-issues.md).
5. **Stop rules for a trial:** a cost limit agreed in advance; destroy everything
   in the same session; if an apply fails partway, destroy what exists before
   investigating for long.
6. **Destroy, then prove it's empty.** List each resource type afterwards; don't
   trust the destroy summary alone. Some things outlive a destroy by design (see
   MOP-AWS-CFN step 7).

`terraform` and `tofu` are interchangeable in every command here.

---

## MOP-AWS-TF — AWS with Terraform

**Run for real:** yes, 54 resources in us-east-2 on 26 Nov 2026 (ADR 0095).

1. **Sign in.** With the AWS CLI v2.32 or later: `aws login --region <region> --profile <name>`, and approve in the browser. Terraform can't read these sessions, so add a profile that asks the CLI for them ([KI-001](known-issues.md#ki-001)):
   ```ini
   # ~/.aws/config
   [profile <name>-terraform]
   credential_process = aws configure export-credentials --profile <name> --format process
   region = <region>
   ```
   Use `export AWS_PROFILE=<name>-terraform` for every Terraform command below.
2. **Check the account can run it.**
   - Plan type: `aws freetier get-account-plan-state --region us-east-1`. A Free plan allows only free-tier instance types ([KI-002](known-issues.md#ki-002)): `aws ec2 describe-instance-types --filters Name=free-tier-eligible,Values=true`.
   - vCPU limit: `aws service-quotas get-service-quota --service-code ec2 --quota-code L-1216C47A`. Must cover the plan's total vCPUs.
3. **Set inputs** in `terraform.tfvars`: `region`; `acm_certificate_arn` to serve HTTPS (empty means HTTP only, and the plan warns); `ami_overrides` only to pin images.
4. `terraform init`, then `terraform plan -out=aws.tfplan`. Read it. Expect only creates.
5. `terraform apply aws.tfplan`. About 3 minutes for a small estate.
6. **Verify:**
   - `terraform plan` → "No changes".
   - No public address on anything behind a load balancer: `aws ec2 describe-instances --query 'Reservations[].Instances[].[Tags[?Key==`Name`]|[0].Value,PublicIpAddress]'`. Only stand-alone public machines may have one ([KI-010](known-issues.md#ki-010)).
   - Disks encrypted at their planned sizes: `aws ec2 describe-volumes --query 'Volumes[].[Size,Encrypted]'`.
   - Load balancer targets healthy: `aws elbv2 describe-target-health --target-group-arn <arn>`. A target only turns healthy once your application answers on its port.
   - Windows: `aws ec2 get-console-output --instance-id <id> --latest` shows "Windows is Ready to use".
7. **Destroy:** `terraform destroy`. Then confirm none left: VPCs (non-default), NAT gateways, load balancers, target groups, Elastic IPs, volumes, instances and IAM roles named after the project.
8. **Sign out** when finished: `aws logout --profile <name>`.

## MOP-AWS-CFN — AWS with CloudFormation

**Run for real:** yes, 27 Nov 2026 (ADR 0096).

1. Sign in as MOP-AWS-TF step 1. The AWS CLI reads `aws login` sessions directly; use `--profile <name>`.
2. Check limits as MOP-AWS-TF step 2.
3. **Find the parameters** the template needs: `README.md` lists them. Images with no AWS shortcut (Red Hat, SUSE, Rocky) need an AMI id:
   ```bash
   aws ec2 describe-images --owners 309956199498 --filters 'Name=name,Values=RHEL-9.*_HVM-*-x86_64-*' --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text
   ```
4. **Deploy:**
   ```bash
   aws cloudformation deploy --template-file template.json --stack-name <stack> --parameter-overrides AmiIdRhel9=<ami> [AcmCertificateArn=<arn>]
   ```
   About 3 minutes. Templates over 51,200 bytes need `--s3-bucket`.
5. **Verify:** deploy the same command again → "No changes to deploy". Then `aws cloudformation detect-stack-drift` and read the result: `IN_SYNC`. Then the checks in MOP-AWS-TF step 6. Expect one boot disk per machine at its planned size: more disks than planned means [KI-020](known-issues.md#ki-020).
6. **Before deleting, note the data disks.** They outlive the stack by design:
   ```bash
   aws ec2 describe-volumes --filters Name=attachment.instance-id,Values=<instance ids> --query 'Volumes[?Attachments[0].DeleteOnTermination==`false`].VolumeId'
   ```
7. **Delete:** `aws cloudformation delete-stack --stack-name <stack>`, wait, then delete the volumes from step 6 once you're sure you don't need them: `aws ec2 delete-volume --volume-id <id>`.

## MOP-AWS-CDK — AWS with CDK

**Run for real:** yes, 27 Nov 2026 (ADR 0096).

1. Sign in as MOP-AWS-CFN step 1. Install Node 22 or later and Python 3.10 or later.
2. `pip install -r requirements.txt` (in a virtual environment).
3. **Bootstrap once per account and region:**
   ```bash
   npx aws-cdk@<version> bootstrap aws://<account>/<region>
   ```
   This creates a `CDKToolkit` stack with a storage bucket and deployment roles.
4. `npx aws-cdk@<version> synth` and read the template.
5. **Deploy:**
   ```bash
   npx aws-cdk@<version> deploy --require-approval never --parameters AmiIdRhel9=<ami> [--parameters AcmCertificateArn=<arn>]
   ```
6. **Verify:** deploy again → "(no changes)"; then MOP-AWS-TF step 6.
7. **Destroy:** note the data disks (MOP-AWS-CFN step 6), `npx aws-cdk@<version> destroy --force`, then delete those disks.
8. **To remove the bootstrap too:** delete the `CDKToolkit` stack. AWS keeps its bucket: empty it (all object versions) and delete it.

## MOP-AWS-PULUMI — AWS with Pulumi

**Run for real:** yes, 27 Nov 2026 (ADR 0096), and again after its routing was added (ADR 0097): 50 resources, the public load balancer reachable from the internet, private machines out through NAT.

1. Sign in as MOP-AWS-TF step 1 and use the `-terraform` profile: the Pulumi AWS provider can't read `aws login` sessions either.
2. **Python environment:** a virtual environment with `pip` in it ([KI-030](known-issues.md#ki-030)), then `pip install -r requirements.txt`.
3. **State:** `pulumi login` (Pulumi Cloud) or `pulumi login file://<folder>` (local). Set `PULUMI_CONFIG_PASSPHRASE` for local state.
4. `pulumi stack init <stack>`; `pulumi config set acmCertificateArn <arn>` to serve HTTPS.
5. `pulumi preview`, read it, then `pulumi up`. About 2.5 minutes.
6. **Verify:** `pulumi preview` → all unchanged; then MOP-AWS-TF step 6.
7. **Destroy:** `pulumi destroy`, then `pulumi stack rm <stack>`.

## MOP-AWS-ADOPT — adopt running AWS servers

**Run for real:** yes, 27 Nov 2026: two running servers in a default network, with public addresses and unencrypted 8 GB disks, adopted, checked and switched to the `for_each` layout without being touched (ADR 0091).

1. Export the fleet with **EC2 instance ids**, as `Instance Name,InstanceId,InstanceType,Platform,VolumeSize`:
   ```bash
   aws ec2 describe-instances --query 'Reservations[].Instances[].[Tags[?Key==`Name`]|[0].Value,InstanceId,InstanceType]' --output text
   ```
   Other ids are not adopted, and the plan says so.
2. Generate. `imports.tf` holds one `import` block per server. Sign in as MOP-AWS-TF step 1.
3. `terraform plan -out=adopt.tfplan`. **Every adopted server must be imported, and nothing may say "must be replaced" or "will be destroyed".** Stop if anything does ([KI-013](known-issues.md#ki-013)).
4. Read each adopted server's changes: `terraform show -json adopt.tfplan`. Expect only `user_data_replace_on_change` (Terraform's own setting) and, if the size changes, `instance_type` (stops and starts the server). Its network, security groups, image, boot disk, public address, role and tags stay as they are ([KI-016](known-issues.md#ki-016)).
5. `terraform apply adopt.tfplan`. Then check the same servers survived: the same instance ids and **the same launch time** (`aws ec2 describe-instances --instance-ids …`), and `terraform plan` → "No changes".
6. The generated network, load balancers and roles are for new servers. Moving an adopted server into it is a replacement: do it deliberately, one at a time, `terraform apply -replace=<address>`.
7. **`terraform destroy` deletes adopted servers too.** To stop managing one without deleting it: `terraform state rm <address>`, then remove its `import` block and resource.

---

## MOP-GCP-TF — Google Cloud with Terraform

**Run for real:** yes, twice: 16 resources (ADR 0084), then 37 with Windows, data disks and both load balancers (ADR 0092).

1. **Sign in for Terraform:** `gcloud auth application-default login`. Tick the Google Cloud box on the consent page ([KI-040](known-issues.md#ki-040)).
2. **Use a separate project for trials.** Turn on the APIs a new project has off:
   ```bash
   gcloud services enable compute.googleapis.com iam.googleapis.com --project <project>
   ```
3. **Check quotas:** `gcloud compute regions describe <region> --project <project>`. A new project has 24 E2 CPUs.
4. Set `gcp_project` in `terraform.tfvars` (and `region`, `zone` if needed).
5. `terraform init`, `terraform plan -out=gcp.tfplan`, read, `terraform apply gcp.tfplan`.
6. **Verify:**
   - `terraform plan` → "No changes" (a stale output value only is fine: `terraform apply -refresh-only`).
   - Machines behind a load balancer have no external address.
   - Internal load balancer backends healthy: `gcloud compute backend-services get-health <name> --region <region>`.
   - Windows: `gcloud compute instances get-serial-port-output <vm> --zone <zone>`.
7. **Destroy:** `terraform destroy`; then list instances, disks, networks, firewall rules, forwarding rules, target pools, routers, instance groups, backend services and health checks. All empty.
8. When finished: `gcloud auth application-default revoke`.

## MOP-GCP-PULUMI — Google Cloud with Pulumi

**Run for real:** yes, 27 Nov 2026: 34 resources, both load balancers and NAT proven (ADR 0097). Windows booted, but didn't report after an early restart ([KI-074](known-issues.md#ki-074)).

1. Sign in as MOP-GCP-TF step 1; enable APIs as step 2.
2. Python environment and state as MOP-AWS-PULUMI steps 2 and 3.
3. `pulumi config set gcpProject <project>`.
4. `pulumi preview`, read, `pulumi up` (about 90 seconds). Verify as MOP-GCP-TF step 6 with `pulumi preview` in place of `terraform plan`: expect all unchanged.
5. Windows machines take several minutes on their first boot: don't restart them before "Windows is Ready to use" is on the serial console.
6. `pulumi destroy`, `pulumi stack rm`.

---

## MOP-AZ-TF — Azure with Terraform

**Run for real:** no.

1. `az login`; `az account set --subscription <id>`.
2. Set `location`, `resource_group_name`, `admin_ssh_public_key` in `terraform.tfvars`. Supply the Windows password from the environment, never from a file: `export TF_VAR_admin_password=...`.
3. Check vCPU quota: `az vm list-usage --location <location> -o table`.
4. `terraform init`, `plan -out`, read, `apply`.
5. Verify as MOP-AWS-TF step 6, with `az vm list -d -o table` for addresses and `az network lb` for probes.
6. `terraform destroy`; then `az group show -n <resource group>` should fail (not found).

## MOP-AZ-BICEP — Azure with Bicep

**Run for real:** no. `bicep build` passes in CI on three inventories (ADR 0093).

1. Sign in as MOP-AZ-TF step 1.
2. `az bicep build --file main.bicep` and read any warnings.
3. Deploy at subscription scope:
   ```bash
   az deployment sub create --location <location> --template-file main.bicep --parameters adminSshPublicKey="$(cat ~/.ssh/id_rsa.pub)" adminPassword=<from a secret store>
   ```
4. Run `az deployment sub what-if` with the same arguments again → no changes.
5. Data disks survive their machine (`deleteOption: Detach`). Delete the resource group to remove everything, including them.

## MOP-AZ-PULUMI — Azure with Pulumi

**Run for real:** no. Runs against the real Pulumi libraries with mocked clouds in CI.

1. Sign in as MOP-AZ-TF step 1. Python environment and state as MOP-AWS-PULUMI steps 2 and 3.
2. `pulumi config set adminSshPublicKey "<key>"`; `pulumi config set --secret adminPassword <password>`.
3. `pulumi preview`, read, `pulumi up`; verify with `pulumi preview`; `pulumi destroy`.

---

## MOP-OCI-TF — Oracle Cloud with Terraform

**Run for real:** no.

1. Create an API signing key for your user (Console → My profile → API keys). Note tenancy OCID, user OCID, fingerprint and the private key path.
2. Set `tenancy_ocid`, `user_ocid`, `fingerprint`, `private_key_path`, `compartment_id`, `region` and `admin_ssh_public_key` in `terraform.tfvars`.
3. Machines are sized in OCPUs, two vCPUs each (ADR 0088). Check your OCPU limits for the shape families in the plan (E4.Flex, E5.Flex).
4. `terraform init`, `plan -out`, read, `apply`.
5. Boot volumes are at least 50 GB, or the image's own size if larger (ADR 0089). SUSE machines get Oracle Linux 9 (ADR 0093).
6. `terraform destroy`; list instances, boot volumes and block volumes in the compartment.

## MOP-DO-TF — DigitalOcean with Terraform

**Run for real:** no.

1. Create a personal access token (API → Tokens). Supply it from the environment: `export TF_VAR_do_token=...`.
2. Upload your SSH key and set `ssh_key_fingerprints` in `terraform.tfvars`.
3. DigitalOcean has no Windows images. Windows machines in the plan are marked "OS FAMILY CHANGED" and will not run as they are (ADR 0023).
4. `terraform init`, `plan -out`, read, `apply`; `terraform destroy`; `doctl compute droplet list` to confirm.

## MOP-NTNX-TF — Nutanix AHV with Terraform

**Run for real:** no.

1. Set `prism_central_endpoint`, `cluster_name`, `nutanix_username`; supply the password from the environment: `export TF_VAR_nutanix_password=...`. Leave `nutanix_insecure = false` unless Prism uses a self-signed certificate you accept.
2. Images are your own: upload them to Prism first and match the names in `compute.tf`. Their size isn't checked against the boot disk.
3. `terraform init`, `plan -out`, read, `apply`; `terraform destroy`.

## MOP-PVE-TF — Proxmox VE with Terraform

**Run for real:** no.

1. Create an API token (Datacenter → Permissions → API Tokens) and supply it from the environment: `export TF_VAR_proxmox_api_token='user@realm!id=secret'`.
2. Set `proxmox_endpoint`, `node_name`, `datastore_id` and `bridge` in `terraform.tfvars`.
3. Proxmox is the one target with static addresses: check the plan's addresses are free on your VLAN.
4. `terraform init`, `plan -out`, read, `apply`; `terraform destroy`.

## MOP-K8S — Kubernetes with KubeVirt

**Run for real:** no.

1. The cluster needs KubeVirt installed and a storage class for the disks.
2. `kubectl apply --dry-run=server -f .` and read it.
3. `kubectl apply -f .`; `kubectl get vmi -A` to watch machines start.
4. `kubectl delete -f .`; check the persistent volume claims are gone if you don't want the data.
