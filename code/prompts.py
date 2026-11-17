"""
Every LLM prompt, and the definitions of the scope and vocabulary levels.

The requests are:

  SCREEN      screening (step 03)
  LOCATE      extraction of the conclusion (step 04)
  DOES        function attribution check (step 04)
  SCOPE       scope (step 05; sees only the masked conclusion)
  VOCABULARY  vocabulary (step 05; sees only the masked conclusion)

The prompts do not mention the study's hypothesis or the comparison
between fields.
"""

import config

DESIGNS = ["animal_experimental", "human_experimental", "human_observational",
           "in_vitro_ex_vivo", "review_meta_analysis", "commentary_theoretical",
           "methods_protocol", "case_report", "ambiguous", "other"]
EMPIRICAL_DESIGNS = {"animal_experimental", "human_experimental",
                     "human_observational", "in_vitro_ex_vivo"}
SCOPES = ["cns", "peripheral", "ambiguous", "neither"]
ROLES = ["focal", "secondary", "absent"]

# ------------------------------------------------------------------- SCREEN pass
SCREEN_SYSTEM = """\
Read the title and abstract of one research article and classify it on two \
independent dimensions. Judge only from the text in front of you; database type \
labels are frequently wrong.

DIMENSION 1 - STUDY DESIGN. Assign the paper to exactly one category:
- animal_experimental: reports the authors' own new data from non-human animals, \
with an experimental manipulation or direct measurement.
- human_experimental: reports the authors' own new data from human participants \
with an intervention or manipulation (drug administration, task manipulation, \
randomised trial).
- human_observational: reports the authors' own new human data without \
manipulation (correlational, genotype association, cohort, imaging or sampling \
with no intervention).
- in_vitro_ex_vivo: reports the authors' own new data from cells, tissue, slices \
or receptor pharmacology only, with no intact organism.
- review_meta_analysis: summarises, critiques or synthesises other people's \
studies; no new primary data of the authors' own.
- commentary_theoretical: argues a position, proposes a hypothesis or presents a \
conceptual framework; no new data.
- methods_protocol: describes a method, assay, tool, instrument or a planned \
study protocol rather than a biological result.
- case_report: one or a few clinical cases.
- ambiguous: you genuinely cannot tell from the abstract whether the authors \
report their own new data - for example the abstract states findings but never \
says what was done, or reads as if it could be either an empirical report or a \
synthesis.
- other: the design is clear but fits none of the categories above.

Important:
1. Choose an empirical category (animal_experimental, human_experimental, \
human_observational, in_vitro_ex_vivo) ONLY if the abstract reports data the \
authors themselves collected or analysed as new work.
2. The decisive test is whether the abstract describes a SAMPLE, PREPARATION OR \
MEASUREMENT the authors themselves used - subjects, animals, cells, participants, \
a task, a dose, an assay, a scan - and reports what came out of it. An abstract \
that states conclusions without ever saying what was done to obtain them is not \
an empirical article.
3. Diagnostic wording for the non-empirical categories, when it describes the \
article itself rather than its background: "this review", "we review", "here we \
review", "narrative review", "systematic review", "scoping review", \
"meta-analysis", "pooled analysis", "we searched PubMed/Embase/Web of Science", \
"studies were identified/included", "this article summarises", "here we \
discuss", and third-person reports of other people's results such as "the \
authors found" or "the authors reported".
4. Do NOT treat ordinary framing language as evidence of a review. Phrases such \
as "accumulating evidence", "several studies suggest", "we propose", "here we \
argue", "it has been postulated" appear routinely in the introduction and \
discussion of ordinary empirical papers. They matter only if such statements are \
the article's OWN principal claims and no new data of the authors' own is \
reported.
5. If you cannot tell whether the authors report their own new data, choose \
ambiguous. Do not guess at a category. Ambiguous is recorded and reviewed \
separately, so an honest "cannot tell" is more useful than a forced choice, and \
it keeps the empirical categories free of false inclusions.
   Use other only when the design IS clear but fits none of the listed \
categories; use ambiguous when the design itself is unclear.

DIMENSION 2 - STUDY SCOPE. Assign the paper to exactly one category:
- cns: the central nervous system is the object of study - brain or spinal cord, \
including behaviour, cognition, circuits, cells, receptors or signalling studied \
there, and human neuroimaging, pharmacological-challenge or behavioural studies.
- peripheral: peripheral or endocrine only - uterus and myometrium, labour and \
parturition, lactation and milk ejection, cardiovascular, gut, bone, kidney, \
tumour cells, or studies whose only measure is a circulating hormone \
concentration.
- ambiguous: you genuinely cannot resolve cns versus peripheral from the \
abstract - for example both are studied without either being the object of the \
headline claim, or the abstract is too sparse to tell.
- neither: no organismal system is the object of study, e.g. pure receptor \
chemistry, assay development, or a purely methodological report.

Important:
6. Choose cns ONLY if the nervous system is genuinely what is being studied. A \
passing mention of behaviour, mood or stress in an otherwise peripheral study \
does not make it cns. A study whose only central claim is inferred from a blood \
or saliva measurement is peripheral. Neuroendocrine reflexes are peripheral: a \
study of a reflex such as suckling-induced prolactin or oxytocin release, milk \
ejection or uterine contraction is peripheral even though the reflex passes \
through the hypothalamus. Likewise, a study whose headline finding is a \
peripheral physiological or endocrine outcome - blood pressure, heart rate, \
fluid or electrolyte balance, plasma hormone or corticosterone levels, \
HPA-axis hormone output - is peripheral, even when the substance acts in the \
brain to produce it.
7. Use ambiguous rather than guessing. It is recorded and reviewed separately, \
so an honest "cannot tell" is more useful than a forced choice.

DIMENSION 3 - ROLE OF THE SUBSTANCE. The SUBSTANCE line above the title names \
one substance. Assign exactly one category:
- focal: the authors' CONCLUSION about their headline finding makes a claim \
about this substance or its system (its neurons, receptors, genes, release or \
signalling), alone or together with other substances.
- secondary: the substance or its system is studied or measured, but the \
conclusion makes no claim about it - it is a side result, a marker, a \
control, or a manipulation that had no effect.
- absent: the substance is only mentioned (background, a cited role, an \
enriched pathway name) and plays no part in the authors' own results.

Important:
8. The test is the conclusion, not how often the name appears. A claim shared \
with another substance counts ("V1aR and OTR pathways converge", "vitamin A \
and oxytocin biomarkers both predict ASD"), and so does a claim about the \
substance's neurons or genes. A paper on another hormone that reports in \
passing that this substance did not change is secondary.

Return the three labels using the tool. Nothing else."""

SCREEN_TOOL = {
    "name": "screen_article",
    "description": "Classify one article by study design, study scope and "
                   "the role of the substance.",
    "input_schema": {
        "type": "object",
        "properties": {
            "design": {"type": "string", "enum": DESIGNS},
            "scope": {"type": "string", "enum": SCOPES},
            "design_uncertain": {"type": "boolean",
                                 "description": "true if the design was a close call"},
            "scope_uncertain": {"type": "boolean"},
            "role": {"type": "string", "enum": ROLES},
            "role_uncertain": {"type": "boolean"},
        },
        "required": ["design", "scope", "design_uncertain", "scope_uncertain",
                     "role", "role_uncertain"],
    },
}

# ------------------------------------------------------------------- LOCATE pass
# Runs only on papers that pass the screen.
EVIDENCE_TYPES = ["behaviour_only", "behaviour_and_mechanism", "mechanism_only",
                  "unclear"]

# One prompt for every paper: it locates the abstract's conclusion (absconc),
# the unit measured. Whether the abstract has labelled sections only helps
# the model find it; it is not treated differently.

_LOCATE_INTRO = """\
You are given the title and abstract of one empirical research article, and \
the SUBSTANCE the analysis is about. Extract where the article's headline finding \
about that substance is concluded.

Throughout: consider ONLY the authors' own novel empirical result. Ignore \
statements about prior studies, background, motivation or future work, even when \
they appear in the same sentence.

1. Identify the single HEADLINE FINDING: the one new result the article is built \
to support. It is usually the abstract's concluding claim. If the title \
foregrounds a different claim than the abstract, note that and let the abstract \
govern.
"""

_LOCATE_WHERE = """
2. Extract, VERBATIM, as absconc, the abstract's CONCLUSION about this \
finding: the sentence or consecutive sentences in which the authors state what \
they conclude from it. If the abstract has labelled sections, the conclusion \
is usually in its Conclusions (or Interpretation) section.
   A CONCLUSION sentence states what the authors take their results to MEAN \
(often "these results suggest", "we conclude", "our findings indicate / \
demonstrate / reveal", "X may ..."). A sentence that REPORTS what was found - \
"we found that", "X increased Y", "was associated with", group differences, \
statistics - is a RESULT, not a conclusion, even when it comes last or states \
the headline finding itself. Never include result sentences in absconc. Only \
if the abstract has no interpretive sentence about the headline finding, take \
the last sentence reporting it.

   Hard requirements:
   - absconc: one sentence, or at most three CONSECUTIVE sentences when the \
conclusion about this finding runs over several. Include only sentences that \
conclude about the headline finding; leave out sentences about other results, \
clinical implications, methods or future work. Copy them as one continuous \
passage, exactly as they stand.
   - absconc_main: when absconc has several sentences, the ONE of them that \
states the main conclusion about the headline finding - an interpretive \
sentence, never a result; preferably the one in which the substance itself \
does something (modulates, regulates, mediates, encodes ...). It must name the \
substance. When absconc is a single sentence, set absconc_main to null.
   - absconc must NAME THE SUBSTANCE given above, by name or by an \
abbreviation the abstract defines for it. A species homologue (vasotocin, \
isotocin, mesotocin) is not the substance. If the conclusion refers to it only \
as "treatment", "the peptide", "the drug" or a pronoun, take another \
CONCLUSION sentence that does name it, never a result. If no conclusion \
sentence names it, set absconc and absconc_main to null. Never return a span \
that does not name it.
   - Do not include a section label such as "Conclusions:".
   Copy the text exactly. Do not paraphrase and do not supply text of your own.

3. Record the ASSAY: the behavioural task, preparation or measurement the finding \
rests on, as the abstract names it, as a short noun phrase (e.g. "three-chamber \
test", "partner preference", "fear conditioning", "slice electrophysiology", \
"intranasal administration + fMRI"). Null if the abstract does not say.

4. Record the EVIDENCE TYPE: which kinds of OUTCOME the abstract reports \
measuring.
   A behavioural outcome is a measured behaviour, task performance, rating or \
self-report (e.g. social approach, freezing, choices, empathy ratings, symptom \
scales).
   A biological outcome is any measurement of the body or brain: neural \
activity, neuroimaging (fMRI, resting-state connectivity, PET, EEG), \
electrophysiology, physiology (blood pressure, heart rate, nerve activity), \
hormone or transmitter levels, gene expression, genetics, cell counts, \
receptor binding.
   What was done TO the subjects does not count as an outcome: a drug, a \
stressor (e.g. forced swim), a stimulus (e.g. suckling). A task whose \
performance is scored IS a behavioural outcome (e.g. a creativity or memory \
test); a task is not an outcome only when it serves to evoke a brain or body \
response and only that response is reported (e.g. a task during fMRI).
   - behaviour_only: only behavioural outcomes;
   - behaviour_and_mechanism: behavioural AND biological outcomes;
   - mechanism_only: only biological outcomes;
   - unclear: cannot be determined from the abstract.

Do not judge whether any claim is true, strong, general or overstated, and do \
not score anything. Use the tool to return your answer."""

LOCATE_SYSTEM = _LOCATE_INTRO + _LOCATE_WHERE

# the one sentence of a multi-sentence conclusion that vocabulary is scored on
MAIN_KEY = config.VOCAB_LOCATION + "_main"

# the conclusion's fields are top-level properties
LOCATE_TOOL = {
    "name": "locate_finding",
    "description": "Extract the headline finding and where it is concluded.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline_finding": {"type": "string"},
            config.VOCAB_LOCATION: {"type": ["string", "null"]},
            MAIN_KEY: {"type": ["string", "null"]},
            "assay": {"type": ["string", "null"]},
            "evidence_type": {"type": "string", "enum": EVIDENCE_TYPES},
            "title_abstract_mismatch": {"type": "boolean"},
            "notes": {"type": ["string", "null"]},
        },
        "required": ["headline_finding", config.VOCAB_LOCATION, MAIN_KEY,
                     "assay", "evidence_type", "title_abstract_mismatch"],
    },
}


# --------------------------------------------------------------- DOES pass
# The function attribution check: one short, separate call per extracted
# conclusion (step 04). Does it make a claim of the form "[substance X] does
# Y"? A conclusion that does not excludes the paper. A separate call, so
# that the judgement rests on the conclusion alone: the model sees the
# substance and the conclusion only.
DOES_SYSTEM = """\
You are given the CONCLUSION of a research abstract and the SUBSTANCE the \
analysis is about. Decide one thing: does the conclusion make a claim of the \
form "SUBSTANCE does Y"?

YES only if, in at least one clause, the substance itself - or its release, \
signalling, activity, levels, receptors, transporter, or the neurons that \
release it - is the ACTOR of a verb that attributes an effect, role or \
function to it: it modulates, regulates, increases, reduces, mediates, \
encodes, controls, drives, promotes, inhibits, is required for, or \
contributes to something. Giving the substance (administration, infusion, \
intranasal dosing) and finding that it changes something also counts \
("intranasal oxytocin reduces craving").

NO when:
- the substance is only context, or a tool or target used to study something \
else ("an agonist of the substance shows that aromatase is regulated by \
steroids");
- something else acts on the substance or its neurons, and the claim is \
about that ("insulin activates oxytocin neurons", "histamine decreases \
serotonin release", "stress depletes dopamine");
- it states only an association, correlation, marker or biomarker ("levels \
are associated with", "receptor methylation may be a predictive marker", \
"a useful phenotype");
- it says only that the substance is involved, implicated, important or \
participates, without saying what it does ("the system is implicated in", \
"supports the importance of the system for");
- it concerns gene variants or genetic influences on the substance's function;
- it is about a change in the amount, density or genotype of the substance's \
receptor or transporter ("transporter density is reduced in older animals"); \
the substance signalling through its receptor does count;
- it reports no effect or no role;
- the effect is on peripheral physiology outside the brain, including any \
action of the substance as a circulating hormone: blood pressure, hormone \
secretion into the blood, antidiuresis, fluid or electrolyte balance, kidney \
function, uterine contraction, milk ejection, ACTH or other pituitary hormone \
release ("the antidiuretic action of vasopressin is expected to prevail"). \
This holds even when the neurons that secrete the hormone were studied in \
the brain;
- it is about a species homologue (vasotocin, isotocin, mesotocin) rather \
than the substance;
- it is about a drug's or treatment's efficacy without saying what the \
substance does ("receptor antagonists showed efficacy in schizophrenia").

Use the tool. Quote the clause that decides it."""

DOES_TOOL = {
    "name": "substance_does",
    "description": "Does the conclusion say what the substance does?",
    "input_schema": {
        "type": "object",
        "properties": {
            "clause": {"type": "string",
                       "description": "the clause that decides it, verbatim"},
            "does": {"type": "boolean"},
        },
        "required": ["clause", "does"],
    },
}


def render_conclusion(substance, conclusion):
    """Input for the DOES pass: the substance and the conclusion only."""
    return f"## SUBSTANCE\n{substance}\n\n## CONCLUSION\n{conclusion}"




# ================================================================== BLIND passes
# The scope and vocabulary levels are defined once, below, and rendered into
# both the LLM's prompts and the coding tool, so the coders and the LLM read
# the same definitions, rules and examples.
#
# Scope and vocabulary are scored in separate calls, so that neither answer
# can influence the other.
#
# Examples are drawn from across all four fields, and are kept to 2-4 per category.

MASK = config.MASK

# One row in both level tables: a statement that does not say what the
# substance does gets no level and no code, and the paper is left out of the
# analysis (step 05).
NOT_DOES = "NA"
NOT_DOES_LEVEL = {
    "key": NOT_DOES, "name": "Not a \"{MASK} does Y\" statement",
    "about": "nothing {MASK} does",
    "definition": ("The statement does not say what {MASK} does: it reports "
                   "only a quantity, a molecular fact or a method; something "
                   "else acts on {MASK}; it states only an association, a "
                   "marker or a null result; it states only where, when or "
                   "in whom an effect of {MASK} was seen, or how that effect "
                   "differed; it is about the amount, density or genotype of "
                   "{MASK}'s receptor or transporter; or the effect is "
                   "peripheral (blood pressure, hormone secretion into the "
                   "blood). The paper is then left out of the analysis."),
    "examples": ["{MASK} levels were higher in females.",
                 "The {MASK} receptor couples to Gq.",
                 "{MASK} transporter density is reduced in older animals.",
                 "The effects of {MASK} on pain thresholds were larger in "
                 "females."]}

SCOPE_AXIS = {
    "key": "scope",
    "title": "Scope — how far does the claim generalize?",
    # the level table's column headers, shared by the model's prompt, the
    # coding tool and the supplement; the third column shows each level's
    # "about"
    "columns": ["Level", "Name", "The claim is about", "Definition",
                "Examples"],
    "intro": (
        "Each level names THE ENTITY THE CLAIM IS ABOUT. The ladder is "
        "monotone and captures how far the scope bound has been removed."),
    "levels": [
        {"key": "0", "name": "Observed assay outcome",
         "about": "one specific measurement",
         "definition": ("States only the recorded endpoint or measurement. "
                        "No latent process is inferred."),
         "examples": ["{MASK} increased the time mice spent in the social "
                      "chamber.",
                      "{MASK} release increased during reward delivery."]},
        {"key": "1", "name": "Construct inferred from the assay",
         "about": "this experiment",
         "definition": ("Names a latent process inferred from the readout, "
                        "still bounded to the specific experiment. The "
                        "construct may be behavioural OR mechanistic."),
         "examples": ["{MASK} increased sociability in the three-chamber test.",
                      "{MASK} encoded a reward-prediction error in this task.",
                      "{MASK} increased spike fidelity in these slices."]},
        {"key": "2", "name": "Region-anchored function",
         "about": "{MASK} placed in a specific region, circuit or cell type",
         "definition": ("States a STANDING function of {MASK} placed in a "
                        "named locus, circuit, pathway or one specific cell "
                        "type: where {MASK} is, is released or acts."),
         "examples": ["Accumbal {MASK} mediates reward learning.",
                      "Dorsal raphe {MASK} controls patience.",
                      "{MASK} rebalances excitation and inhibition in CA2."]},
        {"key": "3", "name": "Substance-wide property",
         "about": "the substance as such",
         "definition": ("States a STANDING function or property with no "
                        "experimental or anatomical bound left. A population "
                        "(a diagnosis, group or species) is not a bound."),
         "examples": ["{MASK} reduces anxiety in people with autism.",
                      "{MASK} encodes reward.",
                      "{MASK} increases the firing of cerebellar Purkinje "
                      "cells.",
                      "{MASK} is a prosocial hormone."]},
        NOT_DOES_LEVEL,
    ],
    "rules": [
        ("NA", "Only for a statement that does not say what {MASK} does "
               "(see its row). A statement that says what {MASK} does always "
               "gets a level, however narrow: a measured outcome is 0."),
        ("0 vs 1", "If the word IS the readout (chamber time, freezing, firing "
                   "rate, latency) it is 0. If it is an inferred construct "
                   "the readout is taken to index (sociability, anxiety, "
                   "reward, impulsivity, signal-to-noise) it is 1."),
        ("1 vs 2", "An experimental bound present (\"increased\", \"in this "
                   "task\", \"in these mice\") caps at 1. Generic present "
                   "tense (\"regulates\", \"controls\", \"mediates\", "
                   "\"gates\") with the experimental bound removed is 2 or "
                   "higher."),
        ("2 vs 3", "A bound is a context placed on {MASK} itself: where it "
                   "is, is released or acts (\"accumbal {MASK}\", \"{MASK} "
                   "in the PVN\", \"{MASK} released in the amygdala\", "
                   "\"{MASK} acting in this circuit\", \"{MASK} rebalances "
                   "excitation and inhibition in CA2\") - that is 2, and a "
                   "circuit named by its function counts (\"{MASK} in "
                   "circuitry for threat detection\"). What {MASK} acts "
                   "ON is not a bound but part of what it does: \"{MASK} "
                   "increases the firing of cerebellar Purkinje cells\" and "
                   "\"{MASK} modulates the salience network\" are 3. How "
                   "{MASK} was given (\"systemic injection of\", "
                   "\"intranasal\", \"infusion of\") is not a bound. \"Central\", \"in "
                   "the brain\" and \"CNS\" are NOT bounds: they name the "
                   "whole nervous system, not a region. If no bound is "
                   "placed on {MASK} it is 3."),
        ("Population", "Who the claim is about does not set the level. A "
                       "population - a diagnosis, clinical group, sex, age "
                       "group or species (\"in BDD\", \"in people at high "
                       "risk\", \"in females\", \"in rats\") - is not a "
                       "bound. Only the experiment and anatomy bound a "
                       "claim. Wording that points back to the study's own "
                       "sample or preparation (\"these patients\", \"our "
                       "participants\", \"in these slices\", \"under "
                       "anaesthesia\") is an experimental bound."),
        ("Conditions", "A manipulated preparation is an experimental bound: "
                       "a knockout, lesion, drug exposure or surgery (\"in "
                       "KO mice\", \"after adrenalectomy\", \"after cocaine "
                       "exposure\"). A natural state is treated like a "
                       "population and is not a bound: pregnancy, lactation, "
                       "ageing or a disease (\"over pregnancy\", \"in "
                       "schizophrenia\")."),
        ("Named task", "Naming the task or behaviour does not bound a "
                       "claim: \"{MASK} modulates marble burying in an "
                       "age-dependent way\" attributes an influence "
                       "to the substance itself and is 3. A claim is bounded "
                       "only by pointers to the study inside the claim "
                       "(past-tense results, \"in this task\", \"in these mice\") and by "
                       "anatomy (region, circuit, pathway, cell type)."),
        ("Hedges", "Confidence hedges (\"may\", \"suggests\", \"might\") "
                   "do NOT lower the level. Neither does stating what {MASK} "
                   "can do rather than what it does: a capability, an "
                   "efficacy or a therapeutic potential is a standing claim "
                   "about {MASK}, and the route, dose or population it was "
                   "given in does not bound it. Framing the claim as a "
                   "finding of the study (\"we show that\", \"our data "
                   "indicate that\", \"this work reveals that\") does not "
                   "bound it either: judge the claim inside the frame. \"Our "
                   "data indicate that {MASK} gates sensory input\" is judged "
                   "on \"{MASK} gates sensory input\"."),
        ("Several", "Read EVERY clause about {MASK}, including subordinate "
                    "ones (\"..., which may be how {MASK} alters "
                    "risk-taking\"). If several generalities "
                    "appear, in one sentence or across sentences, assign the "
                    "HIGHEST level reached."),
    ],
}

VOCAB_AXIS = {
    "key": "vocabulary",
    "title": "Vocabulary — what kind of function is named?",
    "columns": ["Code", "Name", "In short", "Definition", "Examples"],
    "intro": (
        "Classify the words that describe WHAT " + MASK + " DOES - the "
        "function or effect the statement attributes to " + MASK + " itself. "
        "Ignore every other word: the task, the population, other substances, "
        "and what happens around or to " + MASK + ". This is about the kind of "
        "words used, not about the scope of the claim. The main decision is "
        "between BEHAVIOURAL (A or B) and MECHANISTIC (C); NA if the "
        "statement does not say what " + MASK + " does. For a behavioural "
        "function, also say whether it names one kind of behaviour (A) or a "
        "process shared across behaviours (B)."),
    "levels": [
        {"key": "A", "name": "Behavioural: behaviour-specific",
         "about": "one kind of behaviour or capacity",
         "definition": ("Names a specific behaviour, a behavioural domain "
                        "(social behaviour, maternal care, feeding, "
                        "aggression, anxiety, reward, pain), a specific "
                        "cognitive capacity (creativity, intelligence, theory "
                        "of mind, timing) or the paradigm used to measure it. "
                        "TEST: does the label say WHICH KIND of behaviour or "
                        "capacity, rather than a process shared by many "
                        "kinds? YES -> A."),
         "examples": ["{MASK} is a prosocial hormone.",
                      "{MASK} controls partner preference.",
                      "{MASK} is the reward transmitter.",
                      "{MASK} regulates maternal care."]},
        {"key": "B", "name": "Behavioural: domain-general",
         "about": "a process shared across behaviours",
         "definition": ("Names a process of behaviour or cognition that cuts "
                        "across behavioural domains, but is still a statement "
                        "about behaviour or cognition. TEST: would researchers "
                        "studying quite different behaviours recognise it as "
                        "a process of their behaviour too? YES -> B."),
         "examples": ["{MASK} modulates salience.",
                      "{MASK} supports behavioural flexibility.",
                      "{MASK} sets the vigour of ongoing action.",
                      "{MASK} regulates patience."]},
        {"key": "C", "name": "Mechanistic",
         "about": "what it does to neural processing",
         "definition": ("Says what " + MASK + " does to neural processing, "
                        "without naming a behaviour or mental state: an "
                        "operation (gain, gating, excitation-inhibition "
                        "balance), or any effect on neural activity, "
                        "transmission, excitability, plasticity, or the "
                        "function of cells, regions or circuits - even an "
                        "unspecific one (\"modulates the activity of\"). "
                        "TEST: is what " + MASK + " does stated completely "
                        "in terms of neural processing, with no behaviour or "
                        "mental state? YES -> C."),
         "examples": ["{MASK} rebalances excitation and inhibition.",
                      "{MASK} gates sensory input.",
                      "{MASK} sets the gain of cortical responses.",
                      "{MASK} signals prediction error."]},
        NOT_DOES_LEVEL,
    ],
    "rules": [
        ("Order", "First find what {MASK} itself does: the action attached "
                  "directly to it, its own verb and object. Then decide in "
                  "this order. 1) There is none - the statement does not say "
                  "what {MASK} does: NA. 2) That action is an operation on or "
                  "effect in neural processing, stated without behavioural "
                  "or psychological content: C, even when a behavioural "
                  "consequence follows. 3) That action names a behaviour, a "
                  "psychological process, or behavioural or psychological "
                  "content: A or B. \"{MASK} increases the excitability of "
                  "hippocampal interneurons, thereby improving spatial "
                  "memory\" is C; \"{MASK} signals the social value of a "
                  "partner\" is A or B."),
        ("A vs B", "If the term names a particular behaviour, behavioural "
                   "domain or paradigm (\"pair bonding\", \"partner "
                   "preference\", \"maternal care\", \"feeding\", "
                   "\"aggression\", \"anxiety\", \"social behaviour\") or "
                   "a specific cognitive capacity (\"creativity\", \"theory "
                   "of mind\", \"judgment of time\"), it is A. If it names a process that cuts across domains "
                   "(\"salience\", \"flexibility\", \"vigour\", "
                   "\"attention\"), it is B."),
        ("B vs C", "If ANY psychological process, state or behaviour is named "
                   "as the thing being modulated, it is B."),
        ("C vs NA", "Any effect of {MASK} on neural processing is C, from "
                    "a named operation (gain, gating, balance) to an "
                    "unspecific one (\"regulates LC activity\", \"modulates "
                    "the function of orexin neurons\"). An effect on a "
                    "measured behaviour says what {MASK} does, even when "
                    "stated as the readout: \"{MASK} increased time in the "
                    "social chamber\" is A. NA is only for statements that "
                    "do not say what {MASK} does (see its row)."),
        ("Independence", "Judge ONLY the vocabulary. How general or sweeping "
                         "the claim is, is irrelevant here and is scored "
                         "separately."),
        ("Side by side",
         "If the statement attributes an operation on neural processing AND a "
         "behavioural function in parallel, neither one through the other "
         "(\"{MASK} modulates synaptic inputs of pallidal neurons and plays a "
         "role in movement control\"), it is C."),
    ],
}

UNSURE_POLICY = (
    "Always give an answer, even when the decision is close. If it was close, "
    "give your best answer and mark it unsure: unsure is recorded "
    "alongside the answer, never instead of it. When you mark something unsure, "
    "also say which other answer you were weighing it against.")

# vocabulary_group in the analysis tables: behavioural (A, B) or mechanistic
# (C).
VOCAB_GROUPS = {"A": "behavioural", "B": "behavioural", "C": "mechanistic"}

SHARED_PREAMBLE = (
    "You are given ONE scientific statement (one sentence, or a few consecutive "
    "sentences), out of context, from the title or abstract of a neuroscience "
    "paper. The substance under study is written as "
    + MASK + ", and some other signalling substances as " + config.MASK_OTHER
    + "; do not try to work out which substances they are. Judge the "
    "statement exactly as written: not what the authors probably meant, and not "
    "whether the claim is true or well supported.")


def levels_table(axis):
    """The levels as one markdown table. The model's system prompt and the
    supplement both use this; the coding tool draws the same columns and cells
    as an HTML table. Examples are separated by semicolons."""
    cols = axis["columns"]
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for lv in axis["levels"]:
        examples = "; ".join(f'"{e}"' for e in lv["examples"])
        rows.append(f'| {lv["key"]} | {lv["name"]} | {lv["about"]} | '
                    f'{lv["definition"]} | {examples} |')
    return "\n".join(rows)


def _render_axis(axis, closing):
    out = [SHARED_PREAMBLE, "", axis["title"].upper(), axis["intro"], "",
           levels_table(axis), ""]
    out.append("Decision rules:")
    for name, rule in axis["rules"]:
        out.append(f"- {name}: {rule}")
    out += ["", UNSURE_POLICY, "", closing]
    return "\n".join(out)


def _fill(obj):
    """Substitute the mask token into every string of the level definitions."""
    if isinstance(obj, str):
        return obj.replace("{MASK}", MASK)
    if isinstance(obj, list):
        return [_fill(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_fill(v) for v in obj)
    if isinstance(obj, dict):
        return {k: _fill(v) for k, v in obj.items()}
    return obj


SCOPE_AXIS = _fill(SCOPE_AXIS)
VOCAB_AXIS = _fill(VOCAB_AXIS)
SHARED_PREAMBLE = _fill(SHARED_PREAMBLE)
UNSURE_POLICY = _fill(UNSURE_POLICY)

SCOPE_SYSTEM = _render_axis(
    SCOPE_AXIS,
    "Use the tool. Give the exact words that set the level, then the level.")

VOCAB_SYSTEM = _render_axis(
    VOCAB_AXIS,
    "Use the tool. Quote the words naming the function, then classify.")

SCOPE_TOOL = {
    "name": "score_scope",
    "description": "Assign the 0-3 generality level for one statement.",
    "input_schema": {
        "type": "object",
        # justification first: state the evidence before committing to a label
        "properties": {
            "justification_quote": {
                "type": "string",
                "description": "the exact words in the statement that set the level"},
            # levels as strings, like the level table's keys, so that all
            # answers, including "NA", have one type
            "rung": {"type": "string",
                     "enum": ["0", "1", "2", "3", NOT_DOES],
                     "description": "the level, or " + NOT_DOES + " if the "
                                    "statement does not say what the "
                                    "substance does"},
            "uncertain": {"type": "boolean",
                          "description": "true if the decision was close"},
            "alt_rung": {"type": ["string", "null"],
                         "enum": ["0", "1", "2", "3", NOT_DOES, None],
                         "description": "the other level weighed against, "
                                        "when uncertain; null otherwise"},
        },
        "required": ["justification_quote", "rung", "uncertain"],
    },
}

VOCAB_TOOL = {
    "name": "score_vocabulary",
    "description": "Classify the functional vocabulary of one statement.",
    "input_schema": {
        "type": "object",
        "properties": {
            "functional_label": {
                "type": ["string", "null"],
                "description": "the words that say what the substance does, "
                               "verbatim; null if the statement does not "
                               "say what it does"},
            "vocabulary": {"type": "string", "enum": ["A", "B", "C", "NA"]},
            "operation": {
                "type": ["string", "null"],
                "description": "when vocabulary is C, the operation in the "
                               "statement's own words (free text). Not a fixed "
                               "list: operations are clustered after coding so "
                               "no field's taxonomy is imposed on another."},
            "uncertain": {"type": "boolean",
                          "description": "true if the decision was close"},
            "alt_vocabulary": {"type": ["string", "null"],
                               "enum": ["A", "B", "C", "NA", None],
                               "description": "the other code weighed against, "
                                              "when uncertain; null otherwise"},
        },
        "required": ["functional_label", "vocabulary", "uncertain"],
    },
}


# ---------------------------------------------------------------- rendering
def render_paper(paper):
    """Substance + title + abstract, for the screen and locate passes. The
    substance is the paper's field's own name, so every field is told about
    its own substance in the same words."""
    substance = config.FIELDS[paper["meta"]["field"]]["search"]
    return (f"## SUBSTANCE\n{substance}\n\n## TITLE\n{paper['title']}\n\n"
            f"## ABSTRACT\n{paper['abstract']}")
