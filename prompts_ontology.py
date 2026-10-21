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