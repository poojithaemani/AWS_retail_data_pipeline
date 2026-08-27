# Central log group for Glue job output. Short retention: these logs are
# captured into docs/evidence/ during the session and are worthless afterwards.
resource "aws_cloudwatch_log_group" "glue" {
  name              = "/aws-glue/jobs/${var.project}"
  retention_in_days = 3
}
