# Read the analysis tables with their documented types:
#   d <- read_tables(data_dir)   # d$papers, d$spans, d$flow, ...
# data_dir holds analysis/ (a run folder or the unpacked data deposit).
# Column types come from schema.csv, factor levels from levels.csv (field:
# OT first, the reference level). Missing values are empty cells; the answer
# NA (not a "[substance X] does Y" statement) is written not_scorable.

read_tables <- function(data_dir) {
  dir <- file.path(data_dir, "analysis")
  if (!dir.exists(dir)) stop("no analysis tables in ", dir)
  schema <- readr::read_csv(file.path(dir, "schema.csv"), col_types = "ccc",
                            progress = FALSE)
  levels <- readr::read_csv(file.path(dir, "levels.csv"), col_types = "cci",
                            progress = FALSE)
  code <- c(integer = "i", double = "d", character = "c")
  out <- list()
  for (tab in unique(schema$table)) {
    path <- file.path(dir, paste0(tab, ".csv"))
    if (!file.exists(path)) next            # agreement.csv: after human coding
    spec <- schema[schema$table == tab, ]
    d <- readr::read_csv(path, col_types = paste(code[spec$type], collapse = ""),
                         progress = FALSE)
    stopifnot(nrow(readr::problems(d)) == 0)
    for (v in intersect(names(d), unique(levels$variable))) {
      lv <- levels$level[levels$variable == v][order(levels$order[levels$variable == v])]
      d[[v]] <- factor(d[[v]], levels = lv)
    }
    out[[tab]] <- d
  }
  out
}
