# Computational semantic analysis for "Rethinking oxytocin"

Code for the analysis of how oxytocin (OT), vasopressin (VP), dopamine (DA) and serotonin (5-HT) papers attribute functions to the studied substance. Each paper's conclusion is extracted from its abstract, masked, and scored on scope and vocabulary by a large language model (LLM), with human validation. The methods are described in the accompanying manuscript.

## Workflow

Corpus sampling and the computational semantic analysis are a Python pipeline, provided in `code/`; the statistical analysis is an R project, provided in `analysis/`.

| stage | step | code | use |
|-----------------|-----------------|-----------------|---------------------|
| Corpus sampling | 01 | `code/step01_fetch_corpus.py` | random sample per field from OpenAlex; journal criterion (SJR); target size |
|  | 02 | `code/step02_prepare_abstracts.py` | rebuilds each abstract from OpenAlex's inverted index |
| Computational semantic analysis: screening | 03 | `code/step03_screen.py` | screening (LLM) |
| extraction and masking | 04 | `code/step04_locate.py` | extraction (LLM), masking and checks, function attribution check (LLM) |
| blinded semantic scoring | 05 | `code/step05_blind_score.py` | scope and vocabulary (LLM), three repeats each |
| Human validation | 06 | `code/step06_export_for_coders.py` | exports a coding tool for the three human coders |
| Statistical analysis | 07 | `code/step07_join_and_checks.py` | joins the results into the tables the analysis reads |
|  |  | `analysis/analysis.Rmd` | statistics, tables and figures |

`code/prompts.py` holds all prompts, `code/config.py` all settings. `docs/data_dictionary.md` describes the columns of all tables, `docs/supplement_prompts.txt` reproduces all prompts verbatim.

## Getting the code

Run from Terminal:

``` bash
git clone https://github.com/tim-schubert/computational-semantic-analysis.git
cd computational-semantic-analysis
```

All commands below are run from this directory, the repository root.

## Rerunning the pipeline (corpus sampling and computational semantic analysis)

A rerun will not reproduce the paper's numbers exactly: the OpenAlex index changes over time, and the LLM's answers are not deterministic. The study's run is preserved, though: its tables are in the data deposit, and the raw archive can be requested from the corresponding author.

The following commands are for macOS or Linux (on Windows, use WSL).

1.  **Python.** Install Python 3.10, then create an environment and install the packages:

    ``` bash
    python3.10 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    ```

2.  **API keys.** Copy the template, `cp .env.example .env`, and fill in both keys in `.env`:

    -   `OPENALEX_API_KEY`: available from <https://openalex.org/settings/api>.
    -   `ANTHROPIC_API_KEY`: available from <https://console.anthropic.com>.

    For example (not real keys):

    ```         
    ANTHROPIC_API_KEY=sk-ant-api03-EXAMPLE0nly-n0tAr3alKey-XXXXXXXXXXXX
    OPENALEX_API_KEY=EXAMPLEn0tAr3alKeyXXXXXXXX
    ```

3.  **SJR table.** Download the SCImago Journal Rank table (2025 release, "Download data" at <https://www.scimagojr.com/journalrank.php>) and save it in `data/reference/`. Then set `SJR_CSV` in `code/config.py` to its file name (the study's run used a copy downloaded on 24 September 2026, `scimagojr_2025_downloaded_24_sep_2026.csv`).

4.  **Run.**

    ``` bash
    bash code/run_main.sh
    ```

    This runs the pipeline's steps in order and asks before each one, so it can be stopped and resumed (skipping the steps done). Results go to `data/runs/main/`. The LLM steps (03-05) can take several hours each. Without human coding, the last step (07) writes all tables except the human validation.

5.  **Analysis.** Knit the analysis as in [Reproducing the results](#reproducing-the-results-statistical-analysis), but with `data = "../data/runs/main"` (the default) instead of the deposit.

6.  **Human coding (optional).** Step 06 writes `data/runs/main/coding_tool.html`, a page each coder opens in a browser; at the end it saves the coder's codes as `human_coding_<coder>.csv`. Move the coders' files to `data/runs/main/human_coding/` and combine them into one file with the same columns and one header, by hand or by running the following from the repository root (with the environment from step 1 active, `source .venv/bin/activate`):

    ``` bash
    python - <<'EOF'
    import csv, glob
    files = sorted(glob.glob("data/runs/main/human_coding/*.csv"))
    rows = [r for f in files for r in csv.DictReader(open(f, newline="", encoding="utf-8"))]
    with open("data/runs/main/human_coding.csv", "w", newline="", encoding="utf-8") as out:
        w = csv.DictWriter(out, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    EOF
    ```

    Then run step 07 again (and afterwards knit the R analysis again):

    ``` bash
    python code/step07_join_and_checks.py --run main
    ```


## Reproducing the results (statistical analysis)

This needs R (4.4.2 was used, <https://cran.r-project.org>) and RStudio.

1.  Download the data deposit (see the paper's Data availability statement) and unpack it into `data/deposit/`, so that `data/deposit/analysis/papers.csv` exists.

2.  Open `analysis/analysis.Rproj` in RStudio. In the console, install the package versions used:

    ``` r
    install.packages("renv")
    renv::restore()
    ```

    When asked how to proceed, choose "Activate the project and use the project library". This installs the packages into the project only, without changing potential other R installations.

3.  In the console, knit the analysis with the deposit as its data:

    ``` r
    rmarkdown::render("analysis.Rmd", params = list(data = "../data/deposit"))
    ```

    The report is saved as `analysis/analysis.html`, its tables in `analysis/tables/` and figures in `analysis/figures/`.

