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
