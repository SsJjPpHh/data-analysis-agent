# Reference check: missing dependencies are failures, never silent skips.
references <- readLines("scripts/science-reference-packages.txt", warn = FALSE)
references <- references[nzchar(references)]
packages <- sub("^(cran|bioc)::", "", references)
if (!length(packages) || anyDuplicated(packages)) stop("Invalid scientific dependency manifest")
available <- vapply(packages, requireNamespace, logical(1), quietly = TRUE)
if (!all(available)) stop("Missing scientific reference dependencies: ", paste(packages[!available], collapse = ", "))
versions <- vapply(packages, function(p) as.character(utils::packageVersion(p)), character(1))
arguments <- commandArgs(trailingOnly = TRUE)
runtime_path <- if (length(arguments)) arguments[[1]] else "science-runtime.txt"
writeLines(c(R.version.string, paste(names(versions), versions), capture.output(sessionInfo())), runtime_path)
