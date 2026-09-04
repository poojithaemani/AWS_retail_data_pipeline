variable "project" {
  description = "Project name prefix."
  type        = string
  # No default: config/project.env is the single source of truth for this
  # value and exports it as TF_VAR_project. Terraform failing here means the
  # environment was not sourced - run through scripts/de.sh.
}

variable "project_tag" {
  description = "Domain identity for tagging and descriptions. Distinct from the resource name prefix."
  type        = string
  # No default: config/project.env is the single source of truth for this
  # value and exports it as TF_VAR_project_tag. Terraform failing here means the
  # environment was not sourced - run through scripts/de.sh.
}

variable "region" {
  description = "Single pinned AWS region."
  type        = string
  # No default: config/project.env is the single source of truth for this
  # value and exports it as TF_VAR_region. Terraform failing here means the
  # environment was not sourced - run through scripts/de.sh.
}

variable "phase" {
  description = "Phase currently being built; tagged onto every resource."
  type        = string
  default     = "00"
}

variable "state_bucket" {
  description = "Remote state bucket, used to read the persistent layer's outputs."
  type        = string
}

variable "glue_database" {
  description = "Glue Data Catalog database. Named by the training brief (section 4, Day 2)."
  type        = string
  # No default: config/project.env is the single source of truth for this
  # value and exports it as TF_VAR_glue_database. Terraform failing here means the
  # environment was not sourced - run through scripts/de.sh.
}

variable "data_quality_enabled" {
  description = "Create the Glue DQ rulesets. Off by default: a ruleset is bound to a catalog table, and orders_raw/products_raw only exist after crawl and publish have run. See data_quality.tf."
  type        = bool
  default     = false
}

variable "lf_grants_enabled" {
  description = "Create the Lake Formation GOVERNANCE grants: the persona permission matrix, the Marketing column restriction and the LF-Tag demonstration. Separate from lf_pipeline_grants_enabled because those are needed for the pipeline to run at all, while these are the thing being demonstrated. See lakeformation.tf."
  type        = bool
  default     = false
}

variable "lf_pipeline_grants_enabled" {
  description = "Create the Lake Formation grants the PIPELINE requires: the Glue role's DATA_LOCATION_ACCESS, its database permissions for the crawler and publish workflow, and SELECT on the raw tables it reads. Required once the S3 location is registered - IAM_ALLOWED_PRINCIPALS does not cover Lake Formation credential vending, which Gate 2 established by failing without these. See lakeformation.tf."
  type        = bool
  default     = false
}

