variable "project" {
  description = "Project name prefix used for every resource in the account."
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
  description = "Single pinned AWS region. Set from config/project.env."
  type        = string
  # No default: config/project.env is the single source of truth for this
  # value and exports it as TF_VAR_region. Terraform failing here means the
  # environment was not sourced - run through scripts/de.sh.
}

variable "monthly_budget_usd" {
  description = "Hard monthly spend ceiling; alerts fire at 50/80/100%."
  type        = number
  # No default: config/project.env is the single source of truth for this
  # value and exports it as TF_VAR_monthly_budget_usd. Terraform failing here means the
  # environment was not sourced - run through scripts/de.sh.
}

variable "budget_notification_email" {
  description = "Email that receives budget alerts. Required - no silent budgets."
  type        = string

  validation {
    # Character classes rather than backslash escapes: HCL and regex disagree
    # about what a backslash means, and [.] sidesteps the argument.
    condition     = can(regex("^[^@ ]+@[^@ ]+[.][^@ ]+$", var.budget_notification_email))
    error_message = "budget_notification_email must be a valid email address."
  }
}

variable "lake_expiration_days" {
  description = "Days before lake objects expire. Keeps storage cost near zero between rebuilds."
  type        = number
  # No default: config/project.env is the single source of truth for this
  # value and exports it as TF_VAR_lake_expiration_days. Terraform failing here means the
  # environment was not sourced - run through scripts/de.sh.
}

variable "daily_budget_usd" {
  description = "Daily spend ceiling. Catches a failed teardown the next morning, not at month end."
  type        = number
  # No default: config/project.env is the single source of truth for this
  # value and exports it as TF_VAR_daily_budget_usd. Terraform failing here means the
  # environment was not sourced - run through scripts/de.sh.
}

variable "glue_database" {
  description = "Glue Data Catalog database, used to scope the Redshift role's catalog reads."
  type        = string
  # No default: config/project.env is the single source of truth for this
  # value and exports it as TF_VAR_glue_database. Terraform failing here means
  # the environment was not sourced - run through scripts/de.sh.
}

variable "enable_cost_allocation_tag" {
  description = "Activate the Project cost allocation tag in Cost Explorer. Enable on the second apply."
  type        = bool
  default     = false
}

variable "raw_maintenance_principal_arns" {
  description = "Extra principals allowed to delete under raw/. Root and the bootstrapping identity are always included."
  type        = list(string)
  default     = []
}

variable "lf_iam_allowed_principals" {
  description = "Keep Lake Formation's IAM_ALLOWED_PRINCIPALS default. FALSE since Phase 6 step 11 removed it - see D25. It was true while the persona grants were being built and the pipeline re-tested under them, and the default was flipped once that was done: the removal is permanent, so the configuration says so rather than depending on an environment variable being remembered. Setting it back to true would restore the fallback and silently un-enforce every persona grant."
  type        = bool
  default     = false
}

