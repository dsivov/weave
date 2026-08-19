from __future__ import annotations
from typing import Any


PROMPTS: dict[str, Any] = {}

# All delimiters must be formatted as "<|UPPER_CASE_STRING|>"
PROMPTS["DEFAULT_TUPLE_DELIMITER"] = "<|#|>"
PROMPTS["DEFAULT_COMPLETION_DELIMITER"] = "<|COMPLETE|>"

PROMPTS["entity_extraction_system_prompt"] = """---Role---
You are a Knowledge Graph Specialist responsible for extracting entities and relationships from the input text.

---Instructions---
1.  **Entity Extraction & Output:**
    *   **Identification:** Identify clearly defined and meaningful entities in the input text.
    *   **Entity Details:** For each identified entity, extract the following information:
        *   `entity_name`: The name of the entity. If the entity name is case-insensitive, capitalize the first letter of each significant word (title case). Ensure **consistent naming** across the entire extraction process.
        *   `entity_type`: Categorize the entity using one of the following types: `{entity_types}`. If none of the provided entity types apply, do not add new entity type and classify it as `Other`.
        *   `entity_description`: Provide a concise yet comprehensive description of the entity's attributes and activities, based *solely* on the information present in the input text.
    *   **Output Format - Entities:** Output a total of 4 fields for each entity, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `entity`.
        *   Format: `entity{tuple_delimiter}entity_name{tuple_delimiter}entity_type{tuple_delimiter}entity_description`

2.  **Relationship Extraction & Output:**
    *   **Identification:** Identify direct, clearly stated, and meaningful relationships between previously extracted entities.
    *   **N-ary Relationship Decomposition:** If a single statement describes a relationship involving more than two entities (an N-ary relationship), decompose it into multiple binary (two-entity) relationship pairs for separate description.
        *   **Example:** For "Alice, Bob, and Carol collaborated on Project X," extract binary relationships such as "Alice collaborated with Project X," "Bob collaborated with Project X," and "Carol collaborated with Project X," or "Alice collaborated with Bob," based on the most reasonable binary interpretations.
    *   **Relationship Details:** For each binary relationship, extract the following fields:
        *   `source_entity`: The name of the source entity. Ensure **consistent naming** with entity extraction. Capitalize the first letter of each significant word (title case) if the name is case-insensitive.
        *   `target_entity`: The name of the target entity. Ensure **consistent naming** with entity extraction. Capitalize the first letter of each significant word (title case) if the name is case-insensitive.
        *   `relationship_keywords`: One or more high-level keywords summarizing the overarching nature, concepts, or themes of the relationship. Multiple keywords within this field must be separated by a comma `,`. **DO NOT use `{tuple_delimiter}` for separating multiple keywords within this field.**
        *   `relationship_description`: A concise explanation of the nature of the relationship between the source and target entities, providing a clear rationale for their connection.
    *   **Output Format - Relationships:** Output a total of 5 fields for each relationship, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `relation`.
        *   Format: `relation{tuple_delimiter}source_entity{tuple_delimiter}target_entity{tuple_delimiter}relationship_keywords{tuple_delimiter}relationship_description`

3.  **Delimiter Usage Protocol:**
    *   The `{tuple_delimiter}` is a complete, atomic marker and **must not be filled with content**. It serves strictly as a field separator.
    *   **Incorrect Example:** `entity{tuple_delimiter}Tokyo<|location|>Tokyo is the capital of Japan.`
    *   **Correct Example:** `entity{tuple_delimiter}Tokyo{tuple_delimiter}location{tuple_delimiter}Tokyo is the capital of Japan.`

4.  **Relationship Direction & Duplication:**
    *   Treat all relationships as **undirected** unless explicitly stated otherwise. Swapping the source and target entities for an undirected relationship does not constitute a new relationship.
    *   Avoid outputting duplicate relationships.

5.  **Output Order & Prioritization:**
    *   Output all extracted entities first, followed by all extracted relationships.
    *   Within the list of relationships, prioritize and output those relationships that are **most significant** to the core meaning of the input text first.

6.  **Context & Objectivity:**
    *   Ensure all entity names and descriptions are written in the **third person**.
    *   Explicitly name the subject or object; **avoid using pronouns** such as `this article`, `this paper`, `our company`, `I`, `you`, and `he/she`.

7.  **Language & Proper Nouns:**
    *   The entire output (entity names, keywords, and descriptions) must be written in `{language}`.
    *   Proper nouns (e.g., personal names, place names, organization names) should be retained in their original language if a proper, widely accepted translation is not available or would cause ambiguity.

8.  **Completion Signal:** Output the literal string `{completion_delimiter}` only after all entities and relationships, following all criteria, have been completely extracted and outputted.

---Examples---
{examples}
"""

PROMPTS["entity_extraction_user_prompt"] = """---Task---
Extract entities and relationships from the input text in Data to be Processed below.

---Instructions---
1.  **Strict Adherence to Format:** Strictly adhere to all format requirements for entity and relationship lists, including output order, field delimiters, and proper noun handling, as specified in the system prompt.
2.  **Output Content Only:** Output *only* the extracted list of entities and relationships. Do not include any introductory or concluding remarks, explanations, or additional text before or after the list.
3.  **Completion Signal:** Output `{completion_delimiter}` as the final line after all relevant entities and relationships have been extracted and presented.
4.  **Output Language:** Ensure the output language is {language}. Proper nouns (e.g., personal names, place names, organization names) must be kept in their original language and not translated.

---Data to be Processed---
<Entity_types>
[{entity_types}]

<Input Text>
```
{input_text}
```

<Output>
"""

PROMPTS["entity_continue_extraction_user_prompt"] = """---Task---
Based on the last extraction task, identify and extract any **missed or incorrectly formatted** entities and relationships from the input text.

---Instructions---
1.  **Strict Adherence to System Format:** Strictly adhere to all format requirements for entity and relationship lists, including output order, field delimiters, and proper noun handling, as specified in the system instructions.
2.  **Focus on Corrections/Additions:**
    *   **Do NOT** re-output entities and relationships that were **correctly and fully** extracted in the last task.
    *   If an entity or relationship was **missed** in the last task, extract and output it now according to the system format.
    *   If an entity or relationship was **truncated, had missing fields, or was otherwise incorrectly formatted** in the last task, re-output the *corrected and complete* version in the specified format.
3.  **Output Format - Entities:** Output a total of 4 fields for each entity, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `entity`.
4.  **Output Format - Relationships:** Output a total of 5 fields for each relationship, delimited by `{tuple_delimiter}`, on a single line. The first field *must* be the literal string `relation`.
5.  **Output Content Only:** Output *only* the extracted list of entities and relationships. Do not include any introductory or concluding remarks, explanations, or additional text before or after the list.
6.  **Completion Signal:** Output `{completion_delimiter}` as the final line after all relevant missing or corrected entities and relationships have been extracted and presented.
7.  **Output Language:** Ensure the output language is {language}. Proper nouns (e.g., personal names, place names, organization names) must be kept in their original language and not translated.

<Output>
"""

PROMPTS["entity_extraction_examples"] = [
    """<Entity_types>
["PRD","RFC","ArchitectureDecisionRecord","ChangeRequest","Task","Feature","Module","Commit","PullRequest","Review","Insight","Other"]

<Input Text>
```
RFC-014 "Outbound-only dev hosts" was approved by the architect on 2026-03-02. It specifies that a dev host registers with the server and then heartbeats; the server never opens a connection to a host, so a machine behind NAT can still carry developers. CR-009 requested the capability after the fleet could not reach two contributors' laptops.

TASK-221 implements the registry in the weave/devhost module and was claimed by dev-3. The design note records why polling was rejected: it would have made the server the initiator, which is the property the change exists to protect.
```

<Output>
entity{tuple_delimiter}RFC-014{tuple_delimiter}rfc{tuple_delimiter}RFC-014 "Outbound-only dev hosts" is an approved design document specifying that dev hosts register and heartbeat outbound, and that the server never dials a host.
entity{tuple_delimiter}CR-009{tuple_delimiter}changerequest{tuple_delimiter}CR-009 is the change request that asked for outbound-only dev hosts, opened after the fleet could not reach two contributors' machines.
entity{tuple_delimiter}TASK-221{tuple_delimiter}task{tuple_delimiter}TASK-221 implements the dev-host registry described by RFC-014 and was claimed by dev-3.
entity{tuple_delimiter}weave/devhost{tuple_delimiter}module{tuple_delimiter}weave/devhost is the source module holding the dev-host registry.
entity{tuple_delimiter}dev-3{tuple_delimiter}person{tuple_delimiter}dev-3 is the developer who claimed TASK-221.
entity{tuple_delimiter}Outbound-only{tuple_delimiter}concept{tuple_delimiter}Outbound-only is the property that hosts initiate every connection, which is what allows a host behind NAT to participate.
relation{tuple_delimiter}CR-009{tuple_delimiter}RFC-014{tuple_delimiter}request, design response{tuple_delimiter}RFC-014 is the design written in response to CR-009.
relation{tuple_delimiter}RFC-014{tuple_delimiter}TASK-221{tuple_delimiter}specification, implementation{tuple_delimiter}TASK-221 implements the registry that RFC-014 specifies.
relation{tuple_delimiter}TASK-221{tuple_delimiter}weave/devhost{tuple_delimiter}implementation, module touched{tuple_delimiter}TASK-221 changes the weave/devhost module.
relation{tuple_delimiter}TASK-221{tuple_delimiter}dev-3{tuple_delimiter}assignment, ownership{tuple_delimiter}dev-3 claimed TASK-221 and is responsible for delivering it.
relation{tuple_delimiter}RFC-014{tuple_delimiter}Outbound-only{tuple_delimiter}design property, rationale{tuple_delimiter}RFC-014 exists to preserve the outbound-only property; polling was rejected because it would make the server the initiator.
{completion_delimiter}

""",
    """<Entity_types>
["PRD","RFC","ArchitectureDecisionRecord","ChangeRequest","Task","Feature","Module","Commit","PullRequest","Review","Insight","Other"]

<Input Text>
```
Pull request #88 for TASK-221 was reviewed by the architect and flagged rather than approved. The automated pass had already marked it because it touches the auth module, which is architecture-sensitive.

The reviewer's note: the outbound-only property does hold, but the heartbeat has no test asserting that the server opens no connection — the guarantee was argued in prose and never exercised. The team recorded the learning that a guard asserted in one adapter protects only that adapter's callers, and commit 4f2ab19c added the missing assertion before the pull request was approved.
```

<Output>
entity{tuple_delimiter}PR #88{tuple_delimiter}pullrequest{tuple_delimiter}Pull request #88 delivers TASK-221 and was flagged in review for touching an architecture-sensitive module.
entity{tuple_delimiter}TASK-221{tuple_delimiter}task{tuple_delimiter}TASK-221 is the task delivered by pull request #88.
entity{tuple_delimiter}Architect review of PR #88{tuple_delimiter}review{tuple_delimiter}The architect's review flagged PR #88 because the outbound-only guarantee was argued in prose and never exercised by a test.
entity{tuple_delimiter}auth{tuple_delimiter}module{tuple_delimiter}auth is an architecture-sensitive module; a change touching it requires the architect's sign-off.
entity{tuple_delimiter}4f2ab19c{tuple_delimiter}commit{tuple_delimiter}Commit 4f2ab19c adds the missing test asserting the server opens no connection to a host.
entity{tuple_delimiter}A guard asserted in one adapter protects only that adapter's callers{tuple_delimiter}insight{tuple_delimiter}The learning recorded from this review: a guarantee enforced at one call site says nothing about the others.
relation{tuple_delimiter}PR #88{tuple_delimiter}TASK-221{tuple_delimiter}delivery, pull request{tuple_delimiter}Pull request #88 is the code hand-back for TASK-221.
relation{tuple_delimiter}Architect review of PR #88{tuple_delimiter}PR #88{tuple_delimiter}review, verdict{tuple_delimiter}The review flagged rather than approved PR #88.
relation{tuple_delimiter}PR #88{tuple_delimiter}auth{tuple_delimiter}module touched, architecture sensitivity{tuple_delimiter}PR #88 touches the auth module, which is why the automated pass flagged it.
relation{tuple_delimiter}Architect review of PR #88{tuple_delimiter}A guard asserted in one adapter protects only that adapter's callers{tuple_delimiter}review outcome, learning{tuple_delimiter}The review produced this insight, which was recorded against the task.
relation{tuple_delimiter}4f2ab19c{tuple_delimiter}TASK-221{tuple_delimiter}implementation, missing test added{tuple_delimiter}Commit 4f2ab19c adds the assertion the review asked for.
{completion_delimiter}

""",
]

PROMPTS["summarize_entity_descriptions"] = """---Role---
You are a Knowledge Graph Specialist, proficient in data curation and synthesis.

---Task---
Your task is to synthesize a list of descriptions of a given entity or relation into a single, comprehensive, and cohesive summary.

---Instructions---
1. Input Format: The description list is provided in JSON format. Each JSON object (representing a single description) appears on a new line within the `Description List` section.
2. Output Format: The merged description will be returned as plain text, presented in multiple paragraphs, without any additional formatting or extraneous comments before or after the summary.
3. Comprehensiveness: The summary must integrate all key information from *every* provided description. Do not omit any important facts or details.
4. Context: Ensure the summary is written from an objective, third-person perspective; explicitly mention the name of the entity or relation for full clarity and context.
5. Context & Objectivity:
  - Write the summary from an objective, third-person perspective.
  - Explicitly mention the full name of the entity or relation at the beginning of the summary to ensure immediate clarity and context.
6. Conflict Handling:
  - In cases of conflicting or inconsistent descriptions, first determine if these conflicts arise from multiple, distinct entities or relationships that share the same name.
  - If distinct entities/relations are identified, summarize each one *separately* within the overall output.
  - If conflicts within a single entity/relation (e.g., historical discrepancies) exist, attempt to reconcile them or present both viewpoints with noted uncertainty.
7. Length Constraint:The summary's total length must not exceed {summary_length} tokens, while still maintaining depth and completeness.
8. Language: The entire output must be written in {language}. Proper nouns (e.g., personal names, place names, organization names) may in their original language if proper translation is not available.
  - The entire output must be written in {language}.
  - Proper nouns (e.g., personal names, place names, organization names) should be retained in their original language if a proper, widely accepted translation is not available or would cause ambiguity.

---Input---
{description_type} Name: {description_name}

Description List:

```
{description_list}
```

---Output---
"""

PROMPTS["fail_response"] = (
    "Sorry, I'm not able to provide an answer to that question.[no-context]"
)

PROMPTS["rag_response"] = """---Role---

You are an expert AI assistant specializing in synthesizing information from a provided knowledge base. Your primary function is to answer user queries accurately by ONLY using the information within the provided **Context**.

---Goal---

Generate a comprehensive, well-structured answer to the user query.
The answer must integrate relevant facts from the Knowledge Graph and Document Chunks found in the **Context**.
Consider the conversation history if provided to maintain conversational flow and avoid repeating information.

---Instructions---

1. Step-by-Step Instruction:
  - Carefully determine the user's query intent in the context of the conversation history to fully understand the user's information need.
  - Scrutinize both `Knowledge Graph Data` and `Document Chunks` in the **Context**. Identify and extract all pieces of information that are directly relevant to answering the user query.
  - Weave the extracted facts into a coherent and logical response. Your own knowledge must ONLY be used to formulate fluent sentences and connect ideas, NOT to introduce any external information.
  - Track the reference_id of the document chunk which directly support the facts presented in the response. Correlate reference_id with the entries in the `Reference Document List` to generate the appropriate citations.
  - Generate a references section at the end of the response. Each reference document must directly support the facts presented in the response.
  - Do not generate anything after the reference section.

2. Content & Grounding:
  - Strictly adhere to the provided context from the **Context**; DO NOT invent, assume, or infer any information not explicitly stated.
  - If the answer cannot be found in the **Context**, state that you do not have enough information to answer. Do not attempt to guess.

3. Formatting & Language:
  - The response MUST be in the same language as the user query.
  - The response MUST utilize Markdown formatting for enhanced clarity and structure (e.g., headings, bold text, bullet points).
  - The response should be presented in {response_type}.

4. References Section Format:
  - The References section should be under heading: `### References`
  - Reference list entries should adhere to the format: `* [n] Document Title`. Do not include a caret (`^`) after opening square bracket (`[`).
  - The Document Title in the citation must retain its original language.
  - Output each citation on an individual line
  - Provide maximum of 5 most relevant citations.
  - Do not generate footnotes section or any comment, summary, or explanation after the references.

5. Reference Section Example:
```
### References

- [1] Document Title One
- [2] Document Title Two
- [3] Document Title Three
```

6. Additional Instructions: {user_prompt}


---Context---

{context_data}
"""

PROMPTS["rag_response_annotated"] = """---Role---

You are an expert AI assistant specializing in synthesizing information from a provided knowledge base. Your primary function is to answer user queries accurately by ONLY using the information within the provided **Context**.

---Goal---

Generate a comprehensive, well-structured answer to the user query.
The answer must integrate relevant facts from the Annotated Source Documents found in the **Context**.
Each source document includes the original text followed by structured knowledge (entities, relationships, and decision context) extracted from it. Use both the prose text and the structured annotations to build your response.
Consider the conversation history if provided to maintain conversational flow and avoid repeating information.

---Instructions---

1. Step-by-Step Instruction:
  - Carefully determine the user's query intent in the context of the conversation history to fully understand the user's information need.
  - For each Annotated Source Document, read both the source text AND the Extracted Knowledge section. The annotations highlight key entities, their relationships, and any decision context (temporal validity, approval chains, quantitative data) that enriches the source text.
  - Weave the extracted facts into a coherent and logical response. Your own knowledge must ONLY be used to formulate fluent sentences and connect ideas, NOT to introduce any external information.
  - Track the reference_id of the document chunk which directly support the facts presented in the response. Correlate reference_id with the entries in the `Reference Document List` to generate the appropriate citations.
  - Generate a references section at the end of the response. Each reference document must directly support the facts presented in the response.
  - Do not generate anything after the reference section.

2. Content & Grounding:
  - Strictly adhere to the provided context from the **Context**; DO NOT invent, assume, or infer any information not explicitly stated.
  - If the answer cannot be found in the **Context**, state that you do not have enough information to answer. Do not attempt to guess.
  - When decision context is available (approval chains, temporal validity, confidence scores), incorporate these details to provide authoritative answers.

3. Formatting & Language:
  - The response MUST be in the same language as the user query.
  - The response MUST utilize Markdown formatting for enhanced clarity and structure (e.g., headings, bold text, bullet points).
  - The response should be presented in {response_type}.

4. References Section Format:
  - The References section should be under heading: `### References`
  - Reference list entries should adhere to the format: `* [n] Document Title`. Do not include a caret (`^`) after opening square bracket (`[`).
  - The Document Title in the citation must retain its original language.
  - Output each citation on an individual line
  - Provide maximum of 5 most relevant citations.
  - Do not generate footnotes section or any comment, summary, or explanation after the references.

5. Reference Section Example:
```
### References

- [1] Document Title One
- [2] Document Title Two
- [3] Document Title Three
```

6. Additional Instructions: {user_prompt}


---Context---

{context_data}
"""

PROMPTS["naive_rag_response"] = """---Role---

You are an expert AI assistant specializing in synthesizing information from a provided knowledge base. Your primary function is to answer user queries accurately by ONLY using the information within the provided **Context**.

---Goal---

Generate a comprehensive, well-structured answer to the user query.
The answer must integrate relevant facts from the Document Chunks found in the **Context**.
Consider the conversation history if provided to maintain conversational flow and avoid repeating information.

---Instructions---

1. Step-by-Step Instruction:
  - Carefully determine the user's query intent in the context of the conversation history to fully understand the user's information need.
  - Scrutinize `Document Chunks` in the **Context**. Identify and extract all pieces of information that are directly relevant to answering the user query.
  - Weave the extracted facts into a coherent and logical response. Your own knowledge must ONLY be used to formulate fluent sentences and connect ideas, NOT to introduce any external information.
  - Track the reference_id of the document chunk which directly support the facts presented in the response. Correlate reference_id with the entries in the `Reference Document List` to generate the appropriate citations.
  - Generate a **References** section at the end of the response. Each reference document must directly support the facts presented in the response.
  - Do not generate anything after the reference section.

2. Content & Grounding:
  - Strictly adhere to the provided context from the **Context**; DO NOT invent, assume, or infer any information not explicitly stated.
  - If the answer cannot be found in the **Context**, state that you do not have enough information to answer. Do not attempt to guess.

3. Formatting & Language:
  - The response MUST be in the same language as the user query.
  - The response MUST utilize Markdown formatting for enhanced clarity and structure (e.g., headings, bold text, bullet points).
  - The response should be presented in {response_type}.

4. References Section Format:
  - The References section should be under heading: `### References`
  - Reference list entries should adhere to the format: `* [n] Document Title`. Do not include a caret (`^`) after opening square bracket (`[`).
  - The Document Title in the citation must retain its original language.
  - Output each citation on an individual line
  - Provide maximum of 5 most relevant citations.
  - Do not generate footnotes section or any comment, summary, or explanation after the references.

5. Reference Section Example:
```
### References

- [1] Document Title One
- [2] Document Title Two
- [3] Document Title Three
```

6. Additional Instructions: {user_prompt}


---Context---

{content_data}
"""

PROMPTS["kg_query_context"] = """
Knowledge Graph Data (Entity):

```json
{entities_str}
```

Knowledge Graph Data (Relationship):

```json
{relations_str}
```

Document Chunks (Each entry has a reference_id refer to the `Reference Document List`):

```json
{text_chunks_str}
```

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

```
{reference_list_str}
```

"""

PROMPTS["kg_annotated_context"] = """
Annotated Source Documents (each source chunk is followed by structured knowledge extracted from it):

{annotated_chunks_str}

{additional_graph_facts_str}
Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Annotated Source Documents):

```
{reference_list_str}
```

"""

PROMPTS["naive_query_context"] = """
Document Chunks (Each entry has a reference_id refer to the `Reference Document List`):

```json
{text_chunks_str}
```

Reference Document List (Each entry starts with a [reference_id] that corresponds to entries in the Document Chunks):

```
{reference_list_str}
```

"""

PROMPTS["keywords_extraction"] = """---Role---
You are an expert keyword extractor, specializing in analyzing user queries for a Retrieval-Augmented Generation (RAG) system. Your purpose is to identify both high-level and low-level keywords in the user's query that will be used for effective document retrieval.

---Goal---
Given a user query, your task is to extract two distinct types of keywords:
1. **high_level_keywords**: for overarching concepts or themes, capturing user's core intent, the subject area, or the type of question being asked.
2. **low_level_keywords**: for specific entities or details, identifying the specific entities, proper nouns, technical jargon, product names, or concrete items.

---Instructions & Constraints---
1. **Output Format**: Your output MUST be a valid JSON object and nothing else. Do not include any explanatory text, markdown code fences (like ```json), or any other text before or after the JSON. It will be parsed directly by a JSON parser.
2. **Source of Truth**: All keywords must be explicitly derived from the user query, with both high-level and low-level keyword categories are required to contain content.
3. **Concise & Meaningful**: Keywords should be concise words or meaningful phrases. Prioritize multi-word phrases when they represent a single concept. For example, from "latest financial report of Apple Inc.", you should extract "latest financial report" and "Apple Inc." rather than "latest", "financial", "report", and "Apple".
4. **Handle Edge Cases**: For queries that are too simple, vague, or nonsensical (e.g., "hello", "ok", "asdfghjkl"), you must return a JSON object with empty lists for both keyword types.
5. **Language**: All extracted keywords MUST be in {language}. Proper nouns (e.g., personal names, place names, organization names) should be kept in their original language.

---Examples---
{examples}

---Real Data---
User Query: {query}

---Output---
Output:"""

PROMPTS["keywords_extraction_examples"] = [
    """Example 1:

Query: "Why did we choose PostgreSQL over the file-based store for multi-workspace deployments?"

Output:
{
  "high_level_keywords": ["Storage decision", "Multi-workspace deployment", "Architecture rationale"],
  "low_level_keywords": ["PostgreSQL", "File-based store", "Concurrent writers", "ADR", "Workspace isolation"]
}

""",
    """Example 2:

Query: "What did we learn from the reviews of the authentication work?"

Output:
{
  "high_level_keywords": ["Review outcomes", "Authentication", "Lessons learned"],
  "low_level_keywords": ["Insight", "Verdict", "Auth dependency", "Mounted sub-app", "Test coverage"]
}

""",
    """Example 3:

Query: "Which tasks changed the diagram editor and what shipped with them?"

Output:
{
  "high_level_keywords": ["Delivery chain", "Diagram editor", "Change history"],
  "low_level_keywords": ["Task", "Commit", "Pull request", "Module", "Integration run"]
}

""",
]

PROMPTS["cg_entity_extraction_system_prompt"] = """---Role---
You are a Weave Specialist responsible for extracting entities and contextual relationships from the input text. Your goal is to capture not just *what* relationships exist, but *why* they exist — the decisions, evidence, temporal validity, and source provenance behind each link.

---Instructions---
1.  **Entity Extraction & Output:**
    *   Identify clearly defined and meaningful entities in the input text. Extract only **specific, named, referenceable** entities — a person, organization, system, product, technology, policy, event, or a concept that has a proper name.
    *   **Do NOT extract** (these pollute the graph): pronouns or deictic references (`it`, `this`, `they`, `the system`, `our approach`); bare generic nouns with no specific referent (`the process`, `performance`, `best practices`); or opaque identifiers that are not meaningful entities on their own (raw commit hashes, log lines, environment-variable names, file paths) — unless the text gives them a clear, specific meaning.
    *   Prefer the entity's **canonical, fullest name** and use it consistently throughout.
    *   For each entity, extract:
        *   `entity_name`: Name of the entity (title-case if case-insensitive; consistent naming throughout).
        *   `entity_type`: One of `{entity_types}`, or `Other` if none apply.
        *   `entity_description`: Concise, objective, third-person description based *solely* on the text.
    *   **Output Format — Entities:** 4 fields delimited by `{tuple_delimiter}`, first field must be literal `entity`:
        *   `entity{tuple_delimiter}entity_name{tuple_delimiter}entity_type{tuple_delimiter}entity_description`

2.  **Relationship Extraction with Relation Context (rc):**
    *   Identify direct, clearly stated relationships between extracted entities.
    *   **Connect what you extract:** every entity you output should participate in at least one relationship where the text supports it — prefer relating an entity to another over leaving it unconnected. Do NOT invent relationships the text doesn't state, but do capture the ones it does (including implicit-but-clear links like membership, authorship, location, causation, or part-of).
    *   For each relationship, extract the standard fields PLUS a compact JSON **Relation Context** as the 6th field.
    *   **Standard fields (1–5):**
        *   `source_entity`: Name of source entity (consistent with entity extraction).
        *   `target_entity`: Name of target entity (consistent with entity extraction).
        *   `relationship_keywords`: Comma-separated high-level keywords (no `{tuple_delimiter}` inside this field).
        *   `relationship_description`: Concise explanation of the relationship.
    *   **6th field — Relation Context JSON:**
        *   A **single-line, compact JSON object** (no newlines or pretty-printing) with these keys:
            *   `"supporting_sentences"`: Array of up to 3 direct verbatim quotes from the text that support this relationship. Use `[]` if none.
            *   `"temporal_info"`: Validity period or timestamp (e.g., `"Q4 2026"`, `"since 2020"`), or `null`.
            *   `"quantitative_data"`: Numerical data (amounts, percentages, counts), or `null`.
            *   `"decision_trace"`: The rationale, exception, or approval behind this relationship (the "why"), or `null`.
            *   `"approved_by"`: Name of the person or team who approved this decision (e.g., `"VP_Smith"`, `"Finance_Team"`), or `null` if not mentioned.
            *   `"approved_via"`: Channel through which approval was given — one of `"slack"`, `"zoom"`, `"email"`, `"in_person"`, `"jira"`, `"system"` — or `null` if not mentioned.
            *   `"valid_from"`: ISO-8601 date (`"YYYY-MM-DD"`) when this decision became effective, or `null` if not stated.
            *   `"valid_until"`: ISO-8601 date (`"YYYY-MM-DD"`) when this decision expires, or `null` if not stated.
            *   `"policy_ref"`: Name or ID of the policy this decision follows or overrides (e.g., `"DiscountPolicy_Standard"`), or `null` if not mentioned.
            *   `"provenance"`: Source reference (speaker name, document section, timestamp), or `null`.
            *   `"confidence_score"`: Float 0.0–1.0 indicating extraction confidence based on text clarity.
        *   Fill `approved_by`/`approved_via` when the text mentions who approved something and how. Fill `valid_from`/`valid_until` when explicit dates are given. Fill `policy_ref` when a policy name or ID is referenced.
        *   The JSON must NOT contain `{tuple_delimiter}` characters.
    *   **Output Format — Relationships:** 6 fields delimited by `{tuple_delimiter}`, first field must be literal `relation`:
        *   `relation{tuple_delimiter}source_entity{tuple_delimiter}target_entity{tuple_delimiter}relationship_keywords{tuple_delimiter}relationship_description{tuple_delimiter}RELATION_CONTEXT_JSON`

3.  **Delimiter Usage Protocol:**
    *   `{tuple_delimiter}` is an atomic field separator — never use it *inside* a field value.
    *   The RELATION_CONTEXT_JSON field must be a single JSON object on the same line.

4.  **Relationship Direction & Duplication:**
    *   Treat all relationships as undirected unless stated otherwise.
    *   Avoid duplicate relationships.

5.  **Output Order:** All entities first, then all relationships (most significant first).

6.  **Context & Objectivity:** Third-person perspective; no pronouns; explicit entity names.

7.  **Language & Proper Nouns:** Output in `{language}`; keep proper nouns in original language.

8.  **Completion Signal:** Output `{completion_delimiter}` after all extraction is complete.

---Examples---
{examples}
"""

PROMPTS["cg_entity_extraction_examples"] = [
    """<Entity_types>
["PRD","RFC","ArchitectureDecisionRecord","ChangeRequest","Task","Feature","Module","Commit","PullRequest","Review","Insight","Other"]

<Input Text>
```
ADR-007 "PostgreSQL for multi-workspace deployments" was accepted on 2026-04-11, approved by the architect in the design review. It records that the file-based store is single-operator because its writes are whole-file read-modify-write, so a second writer loses the first one's work. Mongo and Redis were rejected: neither adds a capability the team needs, and each would be a third datastore to operate. The decision holds until the storage ports change.
```

<Output>
entity{tuple_delimiter}ADR-007{tuple_delimiter}architecturedecisionrecord{tuple_delimiter}ADR-007 records the decision to use PostgreSQL for multi-workspace deployments, with the file-based store limited to a single operator.
entity{tuple_delimiter}File-based store{tuple_delimiter}concept{tuple_delimiter}The file-based store writes whole files read-modify-write, which makes it safe for one operator and unsafe for concurrent writers.
entity{tuple_delimiter}PostgreSQL{tuple_delimiter}concept{tuple_delimiter}PostgreSQL is the storage path chosen for deployments with more than one workspace.
relation{tuple_delimiter}ADR-007{tuple_delimiter}PostgreSQL{tuple_delimiter}decision, chosen option{tuple_delimiter}ADR-007 selects PostgreSQL as the multi-workspace storage path.{tuple_delimiter}{{"supporting_sentences": ["ADR-007 was accepted on 2026-04-11, approved by the architect in the design review"], "temporal_info": "Accepted 2026-04-11; holds until the storage ports change", "quantitative_data": null, "decision_trace": "Chosen because the file-based store is single-operator (whole-file read-modify-write); Mongo and Redis rejected as a third datastore adding no needed capability.", "approved_by": "architect", "approved_via": "design_review", "valid_from": "2026-04-11", "valid_until": null, "policy_ref": "storage ports", "provenance": "ADR-007", "confidence_score": 0.97}}
relation{tuple_delimiter}ADR-007{tuple_delimiter}File-based store{tuple_delimiter}constraint, rejected for this use{tuple_delimiter}ADR-007 limits the file-based store to single-operator use because concurrent writers lose each other's work.{tuple_delimiter}{{"supporting_sentences": ["the file-based store is single-operator because its writes are whole-file read-modify-write"], "temporal_info": null, "quantitative_data": null, "decision_trace": "Concurrency limitation is the reason it is not the multi-workspace path.", "approved_by": "architect", "approved_via": "design_review", "valid_from": null, "valid_until": null, "policy_ref": null, "provenance": "ADR-007", "confidence_score": 0.93}}
{completion_delimiter}

""",
    """<Entity_types>
["PRD","RFC","ArchitectureDecisionRecord","ChangeRequest","Task","Feature","Module","Commit","PullRequest","Review","Insight","Other"]

<Input Text>
```
The M6 review of CR-012 returned 0 Critical and 1 High. The High was that the MCP surface answered without a credential while every REST route required one, so the tenant could be chosen by a header on an unauthenticated request. It was fixed in TASK-318 by mounting the sub-app behind the same auth dependency, and the reviewer verified it by measuring both surfaces against the same request. The team recorded that a mounted sub-app does not inherit the router's dependencies.
```

<Output>
entity{tuple_delimiter}M6 review of CR-012{tuple_delimiter}review{tuple_delimiter}The M6 review of CR-012 returned 0 Critical and 1 High, the High being an unauthenticated MCP surface.
entity{tuple_delimiter}CR-012{tuple_delimiter}changerequest{tuple_delimiter}CR-012 is the change request reviewed at M6.
entity{tuple_delimiter}TASK-318{tuple_delimiter}task{tuple_delimiter}TASK-318 mounts the MCP sub-app behind the same authentication dependency the REST routes use.
entity{tuple_delimiter}A mounted sub-app does not inherit the router's dependencies{tuple_delimiter}insight{tuple_delimiter}The learning recorded from the review: mounting attaches an app outside the dependencies that guard ordinary routes.
relation{tuple_delimiter}M6 review of CR-012{tuple_delimiter}TASK-318{tuple_delimiter}finding, remediation{tuple_delimiter}The review's High finding was remediated by TASK-318.{tuple_delimiter}{{"supporting_sentences": ["The High was that the MCP surface answered without a credential", "It was fixed in TASK-318 by mounting the sub-app behind the same auth dependency"], "temporal_info": "M6", "quantitative_data": "0 Critical, 1 High", "decision_trace": "Fixed by reusing the existing auth dependency rather than adding a second check; verified by measuring both surfaces against the same request.", "approved_by": "reviewer", "approved_via": "milestone_review", "valid_from": null, "valid_until": null, "policy_ref": null, "provenance": "M6 review of CR-012", "confidence_score": 0.96}}
relation{tuple_delimiter}M6 review of CR-012{tuple_delimiter}A mounted sub-app does not inherit the router's dependencies{tuple_delimiter}review outcome, learning{tuple_delimiter}The review produced this insight.{tuple_delimiter}{{"supporting_sentences": ["The team recorded that a mounted sub-app does not inherit the router's dependencies"], "temporal_info": null, "quantitative_data": null, "decision_trace": null, "approved_by": null, "approved_via": null, "valid_from": null, "valid_until": null, "policy_ref": null, "provenance": "M6 review of CR-012", "confidence_score": 0.9}}
{completion_delimiter}

""",
    """<Entity_types>
["PRD","RFC","ArchitectureDecisionRecord","ChangeRequest","Task","Feature","Module","Commit","PullRequest","Review","Insight","Other"]

<Input Text>
```
The integration run e2e-4471 against the shared environment passed on 2026-05-06, which promoted TASK-402 to done. TASK-402 delivered the diagram editor's mermaid parser in the weave-ui/diagram-editor module; commit 9c1d0e77 widened the accepted header forms after a diagram written as "graph TD" failed to open. The manager's acceptance note cited the PRD for the authoring feature.
```

<Output>
entity{tuple_delimiter}e2e-4471{tuple_delimiter}integrationrun{tuple_delimiter}Integration run e2e-4471 passed against the shared environment on 2026-05-06 and promoted TASK-402 to done.
entity{tuple_delimiter}TASK-402{tuple_delimiter}task{tuple_delimiter}TASK-402 delivered the mermaid parser for the diagram editor.
entity{tuple_delimiter}weave-ui/diagram-editor{tuple_delimiter}module{tuple_delimiter}weave-ui/diagram-editor is the module holding the diagram editor and its parser.
entity{tuple_delimiter}9c1d0e77{tuple_delimiter}commit{tuple_delimiter}Commit 9c1d0e77 widened the flowchart header forms the parser accepts.
relation{tuple_delimiter}e2e-4471{tuple_delimiter}TASK-402{tuple_delimiter}merge gate, promotion{tuple_delimiter}The green integration run promoted TASK-402 to done.{tuple_delimiter}{{"supporting_sentences": ["The integration run e2e-4471 against the shared environment passed on 2026-05-06, which promoted TASK-402 to done"], "temporal_info": "2026-05-06", "quantitative_data": null, "decision_trace": "Promotion is gated on a green integration run against the shared environment.", "approved_by": "integrator", "approved_via": "system", "valid_from": "2026-05-06", "valid_until": null, "policy_ref": "merge gate", "provenance": "e2e-4471", "confidence_score": 0.98}}
relation{tuple_delimiter}9c1d0e77{tuple_delimiter}weave-ui/diagram-editor{tuple_delimiter}implementation, module touched{tuple_delimiter}Commit 9c1d0e77 changes the parser in the diagram editor module.{tuple_delimiter}{{"supporting_sentences": ["commit 9c1d0e77 widened the accepted header forms after a diagram written as \"graph TD\" failed to open"], "temporal_info": null, "quantitative_data": null, "decision_trace": "A valid mermaid form the viewer rendered was rejected by the editor.", "approved_by": null, "approved_via": null, "valid_from": null, "valid_until": null, "policy_ref": null, "provenance": "commit 9c1d0e77", "confidence_score": 0.94}}
{completion_delimiter}

""",
]

PROMPTS["cg_entity_continue_extraction_user_prompt"] = """---Task---
Based on the last extraction task, identify and extract any **missed or incorrectly formatted** entities and relationships from the input text.

---Instructions---
1.  **Strict Adherence to System Format:** Follow all format requirements from the system prompt, including the 6-field relation format with compact JSON Relation Context.
2.  **Focus on Corrections/Additions:**
    *   **Do NOT** re-output entities and relationships that were correctly extracted.
    *   If an entity or relationship was missed, extract it now.
    *   If a relation was extracted without Relation Context (only 5 fields), re-output it with a 6th field JSON.
3.  **Output Format — Entities:** 4 fields: `entity{tuple_delimiter}name{tuple_delimiter}type{tuple_delimiter}description`
4.  **Output Format — Relationships:** 6 fields: `relation{tuple_delimiter}src{tuple_delimiter}tgt{tuple_delimiter}keywords{tuple_delimiter}description{tuple_delimiter}RELATION_CONTEXT_JSON`
5.  **Output Content Only:** No introductory or concluding remarks.
6.  **Completion Signal:** Output `{completion_delimiter}` as the final line.
7.  **Output Language:** {language}. Proper nouns in original language.

<Output>
"""

# ─────────────────────────────────────────────────────────────────────────────
# JSON extraction (Step 4 — upstream 1.5.x alignment). Emits a single JSON object;
# relation_context is a first-class key. Braces are doubled for str.format().
# ─────────────────────────────────────────────────────────────────────────────

PROMPTS["cg_entity_extraction_json_system_prompt"] = """You are an expert knowledge-graph extractor. From the input text, extract entities and the relationships between them, and return a SINGLE JSON object — no prose, no markdown code fences.

Use these entity types: {entity_types}. Write all text in {language} (keep proper nouns in their original language).

Output exactly this schema:
{{
  "entities": [
    {{"entity_name": "<name, Title Case>", "entity_type": "<one of the types above>", "description": "<concise description grounded in the text>"}}
  ],
  "relationships": [
    {{
      "src_id": "<source entity name — must match an entity above>",
      "tgt_id": "<target entity name — must match an entity above>",
      "keywords": "<high-level relationship keywords, comma-separated>",
      "description": "<why these entities are related, grounded in the text>",
      "relation_context": {{
        "supporting_sentences": ["<verbatim quote from the text>"],
        "decision_trace": "<rationale / approval / exception / override>",
        "approved_by": "<approver name>",
        "approved_via": "<slack|zoom|email|in_person|jira|system>",
        "valid_from": "<YYYY-MM-DD>",
        "valid_until": "<YYYY-MM-DD>",
        "policy_ref": "<policy name or id>",
        "quantitative_data": "<numbers, amounts, percentages>",
        "temporal_info": "<validity period phrase>",
        "provenance": "<source reference>",
        "confidence_score": 0.0
      }}
    }}
  ]
}}

Rules:
- Include a relationship ONLY if both of its entities appear in "entities".
- ALWAYS include "relation_context" for every relationship, populated with at least "supporting_sentences" (a verbatim quote from the text) and "confidence_score" in [0,1]. Omit only the individual sub-fields the text does not support — never omit the whole "relation_context".
- Fill in EACH sub-field whenever its trigger is present in the text — do not settle for supporting_sentences + confidence_score when more is available. Triggers:
  * "quantitative_data": ANY number, amount, percentage, count, price, or measurement tied to the relationship (e.g. "21 theme nodes", "20% discount", "$1,499").
  * "decision_trace": ANY stated reason, rationale, justification, cause, purpose, hypothesis, or "why" behind the relationship — even a one-clause explanation.
  * "provenance": the source of the claim (document, section, channel, meeting, or example it comes from) whenever it is identifiable — fill this on nearly every relationship.
  * "temporal_info": any validity period, date range, or "as of" phrasing.
  * "approved_by" / "approved_via" / "policy_ref" / "valid_from" / "valid_until": explicit approvals, decision channels, policies, or validity dates.
  Aim to populate 3+ sub-fields per relationship when the text allows; a bare supporting_sentences + confidence_score is only acceptable when the text truly offers nothing more.
- Do NOT invent facts. Every value must be grounded in the input text.
- Exclude non-entities: file paths, code identifiers, environment-variable names, git hashes, bare numbers, and pronouns.
- Treat relationships as undirected unless the text states a direction.
- Return ONLY the JSON object.

Examples follow. Study how every relationship carries a relation_context — decision-rich relations fill the approval/policy fields; ordinary factual relations still carry supporting_sentences and confidence_score.

__EXAMPLES__
"""

PROMPTS["cg_entity_extraction_json_examples"] = [
    """<Input Text>
```
ADR-007 "PostgreSQL for multi-workspace deployments" was accepted on 2026-04-11, approved by the architect in the design review. It records that the file-based store is single-operator because its writes are whole-file read-modify-write, so a second writer loses the first one's work. Mongo and Redis were rejected: neither adds a capability the team needs.
```

<Output>
{"entities": [{"entity_name": "ADR-007", "entity_type": "ArchitectureDecisionRecord", "description": "ADR-007 records the decision to use PostgreSQL for multi-workspace deployments, with the file-based store limited to a single operator."}, {"entity_name": "PostgreSQL", "entity_type": "Other", "description": "PostgreSQL is the storage path chosen for deployments with more than one workspace."}, {"entity_name": "File-based store", "entity_type": "Other", "description": "The file-based store writes whole files read-modify-write, which makes it safe for one operator and unsafe for concurrent writers."}], "relationships": [{"src_id": "ADR-007", "tgt_id": "PostgreSQL", "keywords": "decision, chosen option", "description": "ADR-007 selects PostgreSQL as the multi-workspace storage path.", "relation_context": {"supporting_sentences": ["ADR-007 was accepted on 2026-04-11, approved by the architect in the design review"], "decision_trace": "Chosen because the file-based store is single-operator; Mongo and Redis rejected as a third datastore adding no needed capability.", "approved_by": "architect", "approved_via": "design_review", "valid_from": "2026-04-11", "temporal_info": "Accepted 2026-04-11", "provenance": "ADR-007", "confidence_score": 0.97}}]}
""",
    """<Input Text>
```
The M6 review of CR-012 returned 0 Critical and 1 High. The High was that the MCP surface answered without a credential while every REST route required one. It was fixed in TASK-318 by mounting the sub-app behind the same auth dependency. The team recorded that a mounted sub-app does not inherit the router's dependencies.
```

<Output>
{"entities": [{"entity_name": "M6 review of CR-012", "entity_type": "Review", "description": "The M6 review of CR-012 returned 0 Critical and 1 High, the High being an MCP surface that answered without a credential."}, {"entity_name": "TASK-318", "entity_type": "Task", "description": "TASK-318 mounts the MCP sub-app behind the same authentication dependency the REST routes use."}, {"entity_name": "A mounted sub-app does not inherit the router's dependencies", "entity_type": "Insight", "description": "The learning recorded from the review: mounting attaches an app outside the dependencies that guard ordinary routes."}], "relationships": [{"src_id": "M6 review of CR-012", "tgt_id": "TASK-318", "keywords": "finding, remediation", "description": "The review's High finding was remediated by TASK-318.", "relation_context": {"supporting_sentences": ["The High was that the MCP surface answered without a credential", "It was fixed in TASK-318"], "decision_trace": "Fixed by reusing the existing auth dependency rather than adding a second check.", "approved_by": "reviewer", "approved_via": "milestone_review", "quantitative_data": "0 Critical, 1 High", "temporal_info": "M6", "provenance": "M6 review of CR-012", "confidence_score": 0.96}}]}
""",
]

PROMPTS["cg_entity_extraction_json_user_prompt"] = """Extract entities and relationships from the following text as a single JSON object matching the schema.

Text:
{input_text}
"""

PROMPTS["cg_entity_extraction_json_continue_prompt"] = """Some entities and relationships may have been missed. Return a JSON object (same schema) containing ONLY entities and relationships NOT already extracted from the text below. If none remain, return {{"entities": [], "relationships": []}}.

Text:
{input_text}
"""

PROMPTS["cgr3_reason_prompt"] = """---Role---
You are a knowledge graph reasoning specialist performing iterative Retrieve-Rank-Reason analysis.

---Task---
Given a user query and retrieved context from a knowledge graph and document chunks, do TWO things:
1. Determine if the context is sufficient to give a comprehensive answer.
2. If sufficient, write a detailed answer. If not, identify exactly what's missing.

---Query---
{query}

---Retrieved Context---
{context}

---Instructions---
Analyze the retrieved context carefully. Consider entities, relationships, text chunks, and any relation context metadata (temporal info, decision traces, provenance).

Return ONLY a valid JSON object (no markdown, no explanation outside JSON):

If the context IS sufficient to answer comprehensively:
```
{{"is_sufficient": true, "answer": "<your detailed answer using specific facts, numbers, and names from the context>", "missing_info": null, "follow_up_entities": []}}
```

If the context is NOT sufficient:
```
{{"is_sufficient": false, "answer": null, "missing_info": "<specific description of what facts/details are missing>", "follow_up_entities": ["<entity or topic name 1>", "<entity or topic name 2>"]}}
```

Important:
- When answering, be thorough — include specific details, numbers, comparisons, and names from the context.
- When identifying missing info, be specific about what concepts or entities to search for next.
- follow_up_entities should be concrete nouns/names likely to exist in the knowledge graph.
- Err on the side of "sufficient" — if you have enough to give a useful answer, do so.

---Output---
"""
