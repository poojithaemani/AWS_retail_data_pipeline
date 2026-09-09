# ---------------------------------------------------------------------------
# Redshift Serverless - the analytical warehouse. Phase 7.
#
# In the TRAINING layer, deliberately. Redshift is the first component in this
# project whose idle existence is not free: Serverless bills RPU-seconds while
# queries run and holds managed storage in between. A namespace left in the
# persistent layer would survive `down` and quietly accrue, which is exactly
# what the teardown rule exists to prevent.
#
# It consumes the curated lake and changes nothing about it. No Lake Formation
# control, no Glue resource, no S3 permission and no KMS configuration is
# touched by this file - Phase 6 established how the lake is governed, and a
# warehouse reading from it has no business renegotiating that.
#
# TWO ACCESS PATHS, WHICH IS THE POINT OF THE PHASE
# -------------------------------------------------
#   COPY      reads S3 objects directly, authorised by the Redshift role's IAM
#             policy. Lake Formation is not consulted.
#   Spectrum  reads through the Glue Catalog as an external schema, and IS
#             governed by Lake Formation.
#
# Same bytes, two authorisation models. After Phase 6 removed
# IAM_ALLOWED_PRINCIPALS, only the first of those works on the permissions this
# role has today - which is a finding to demonstrate rather than to pre-empt, so
# nothing here grants Spectrum anything.
# ---------------------------------------------------------------------------

# Networking by discovery, never by literal.
#
# A workgroup needs subnets in at least three availability zones and a security
# group. Writing the ids in would tie the training layer to one account and one
# region - it is rebuilt from scratch every session and must not carry a
# hardcoded subnet from whichever account it was first written in.
#
# These are data sources, so nothing is created: no VPC, no subnet, no security
# group, and emphatically no NAT gateway. The Data API is a control-plane
# endpoint, so the warehouse needs no route to the internet at all.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# The VPC's own default security group. Referenced rather than created: with no
# public endpoint and no inbound client, a bespoke group would differ from this
# one only in name.
data "aws_security_group" "default" {
  vpc_id = data.aws_vpc.default.id
  name   = "default"
}

resource "aws_redshiftserverless_namespace" "warehouse" {
  namespace_name = "${var.project}-warehouse"
  db_name        = "retail"

  # The role that COPY assumes to read curated Parquet. Already scoped in the
  # persistent layer to curated/* plus the lake key - see the ReadCuratedObjects
  # and DecryptCuratedObjects statements. Nothing is widened here.
  default_iam_role_arn = local.redshift_role
  iam_roles            = [local.redshift_role]

  # Let AWS generate and hold the admin credential. The Data API authenticates
  # with the caller's IAM identity, so nothing in this project ever needs the
  # password - and a password nobody needs is a password that should not exist
  # in Terraform state, a tfvars file or the shell history.
  manage_admin_password = true

  tags = {
    Name    = "${var.project}-warehouse"
    Purpose = "Retail star schema over the curated sales fact"
  }
}

resource "aws_redshiftserverless_workgroup" "warehouse" {
  namespace_name = aws_redshiftserverless_namespace.warehouse.namespace_name
  workgroup_name = "${var.project}-warehouse-wg"

  # The floor. 8 RPU is the smallest Serverless accepts, and the dataset is
  # twelve thousand rows - more capacity would finish the same queries in the
  # same second and bill more for it.
  base_capacity = 8

  subnet_ids         = data.aws_subnets.default.ids
  security_group_ids = [data.aws_security_group.default.id]

  # No public endpoint. Everything reaches this through the Redshift Data API,
  # which is why there is no JDBC driver, no client subnet and no NAT anywhere
  # in this phase.
  publicly_accessible = false

  tags = {
    Name    = "${var.project}-warehouse-wg"
    Purpose = "Query endpoint for the retail warehouse"
  }
}
