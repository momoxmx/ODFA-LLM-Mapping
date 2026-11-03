PLACE_PROMPT_V1 = """You are mapping questionnaire items to classes in the Ontology of Dental Care-Related Fear and Anxiety (ODFA).

Below is the complete set of ODFA question classes available for this task. Each line gives a class ID, its label, and its definition. Indentation indicates parent-child relations.

{tree}

Questionnaire item:
{item}

Identify the most appropriate existing ODFA class or classes for this item. Choose the most specific class when possible. An item may map to more than one class.

If no existing class adequately captures the item's content, propose one new class and identify its most appropriate parent class.

Return only valid JSON:
{{
  "nodes": ["<class id>", ...],
  "new_node": null or {{
    "label": "<new class label>",
    "definition": "<new class definition>",
    "parent_id": "<existing class id or null>"
  }},
  "rationale": "<one short sentence>"
}}"""

PLACE_PROMPT_V2 = """You are mapping questionnaire items to classes in the Ontology of Dental Care-Related Fear and Anxiety (ODFA).

Below is the complete set of ODFA question classes available for this task. Each line gives a class ID, its label, and its definition. Indentation indicates parent-child relations.

{tree}

Questionnaire item:
{item}

Identify the most appropriate existing ODFA class or classes for this item. Choose the most specific class or classes that capture the item's content.

Return only valid JSON:
{{
  "nodes": ["<class id>", ...],
  "rationale": "<one short sentence>"
}}"""

PLACE_PROMPT = """You are mapping questionnaire items to classes in the Ontology of Dental Care-Related Fear and Anxiety (ODFA).

Below is the complete set of ODFA question classes available for this task. Each line gives a class ID, its label, and its definition. Indentation indicates parent-child relations.

{tree}

Questionnaire item:
{item}

Identify the single most appropriate existing ODFA class for this item. Assign more than one class only if the item clearly represents multiple distinct concepts.

Return only valid JSON:
{{
  "nodes": ["<class id>", ...],
  "rationale": "<one short sentence>"
}}"""

# ---------- V4 / V5: question tree + concept tree + question-class axioms + concept axioms ----------
# Both use the same ontology input (built by ontology.ontology_context).
# V4 may only use existing classes; V5 may also propose a new class.

ONTOLOGY_INPUT = """You are mapping questionnaire items to question classes in the Ontology of Dental Care-Related Fear and Anxiety (ODFA).

PART 1. QUESTION CLASS HIERARCHY
The question classes you can choose from. Each line gives a class ID, label and definition. Indentation shows parent-child relations.

{question_tree}

PART 2. RELATED CONCEPT HIERARCHY
The concepts that the question classes are about. Each line gives a concept ID, label and definition. Indentation shows parent-child relations ("is a"). A concept with more than one parent is listed under each parent; the repeated entry is marked "(see above)".

{concept_tree}

PART 3. LOGICAL AXIOMS
How each question class is defined in terms of the concepts in Part 2.
"A ≡ B" means A and B are logically equivalent: they describe exactly the same set of entities.
"A ⊑ B" means A is a subclass of B: every instance of A is also an instance of B, but not necessarily vice versa.
"is about some X" is satisfied when the question is about X or about any subclass of X in Part 2.

{axioms}

PART 4. LOGICAL AXIOMS OF THE RELATED CONCEPTS
How the concepts in Part 2 are themselves defined and related to other classes, using the same OWL notation as Part 3.

{concept_axioms}

QUESTIONNAIRE ITEM
{item}
"""

RULES = """
How to decide:
1. Work out what the item actually asks about or measures; do not match on keywords alone. Find the most specific matching concepts in Part 2.
2. Use the axioms in Parts 3 and 4 and the hierarchies in Parts 1 and 2 to find every question class whose definition the item satisfies.
3. Reason only from the definitions and relations given above. Do not add relations that the ontology does not state, even if they seem true from general knowledge.
4. Keep only the most specific class on each branch: if you choose a class, do not also choose its ancestor. If the item satisfies several unrelated classes, choose all of them.
"""

PLACE_PROMPT_V4 = ONTOLOGY_INPUT + RULES + """5. If no existing class is satisfied, return an empty list for "nodes". Do not propose new classes.

Return only valid JSON:
{{
  "concepts": ["<Part 2 concept ID>", ...],
  "nodes": ["<question class ID>", ...],
  "rationale": "<one sentence giving the reasoning path, e.g. item asks about X -> X is a Y -> class Z is defined as a question about Y>"
}}"""

PLACE_PROMPT_V5 = ONTOLOGY_INPUT + RULES + """5. If the item satisfies an existing class, use the existing class.
6. Only if no existing class adequately captures what the item asks about, propose one new question class. A new class should be general enough to group several questionnaire items, not tailored to this one item. Define it in the same form as Part 3, using an existing Part 2 concept where possible. If no existing concept fits, also propose the new concept it is about.

Return only valid JSON:
{{
  "concepts": ["<Part 2 concept ID>", ...],
  "nodes": ["<existing question class ID>", ...],
  "new_node": null or {{
    "label": "<new question class label>",
    "definition": "<text definition>",
    "parent_id": "<existing question class ID or null>",
    "logical_definition": "question AND (is about some <concept ID, or the label of the new concept>)",
    "new_concept": null or {{
      "label": "<new concept label>",
      "definition": "<definition>",
      "parent_concept_id": "<Part 2 concept ID>"
    }}
  }},
  "rationale": "<one sentence giving the reasoning path>"
}}"""

