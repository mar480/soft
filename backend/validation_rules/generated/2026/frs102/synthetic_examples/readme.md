1. Sync between repo versions:
   - Updated [synthetic_model_generator.py](</C:/Users/rmarks/OneDrive - Companies House/Desktop/soft/backend/validation_rules/testing/synthetic_model_generator.py:1>)
   - Updated [rule_execution.py](</C:/Users/rmarks/OneDrive - Companies House/Desktop/soft/backend/validation_rules/testing/rule_execution.py:1>)
   - Added [rule_execution_batch.py](</C:/Users/rmarks/OneDrive - Companies House/Desktop/soft/backend/validation_rules/testing/rule_execution_batch.py:1>)
   - Generated and verified new synthetic artifacts under [synthetic_examples](</C:/Users/rmarks/OneDrive - Companies House/Desktop/soft/backend/validation_rules/generated/2026/frs102/synthetic_examples:1>), including:
     - `ppe_note_invalid_hypercube.html`
     - `ppe_note_invalid_hypercube.json`
     - `ppe_note_invalid_hypercube_results.json`

2. Project plan phase:
   - This is Phase 4
   - The Phase 4 feature is now a loader-first testing pipeline: synthetic XHTML and real filings both feed one internal report model before rule execution

3. Feature description from project plan:
   - Added a Family 2 negative synthetic case that deliberately breaks allowed hypercube combinations
   - Added a small batch runner for directories of real or synthetic filings
   - Tightened Family 1 note-detection so real filings can satisfy note presence from topic-dimension evidence even when synthetic-only `data-topic-id` is absent
   - Kept topic relevance narrower so dimension overlap alone does not light up unrelated topics

4. Completion measure and next-turn to-dos:
   - New Family 2 case works:
     - `ppe_note_invalid_hypercube` now fails only `property_plant_equipment.HYPERCUBE_CONFORMITY`
   - Batch runner works:
     - verified across `7` synthetic files
     - aggregate result: `1` clean pass file, `6` targeted failure files
   - Family 1 heuristics improved:
     - note evidence can now come from topic dimensions from Families 1/2/3
     - but topic activation still requires either strong topic evidence or statement-trigger evidence, which removed the earlier cross-topic noise

   How to generate synthetic files:
   - Preset profile:
     ```powershell
     python -m backend.validation_rules.testing.synthetic_model_generator --profile ppe_note_minimal --json-output backend/validation_rules/generated/2026/frs102/synthetic_examples/ppe_note_minimal.json
     ```
   - Direct topic/statement generation:
     ```powershell
     python -m backend.validation_rules.testing.synthetic_model_generator --topic-id property_plant_equipment --concept-qname core:PropertyPlantEquipment
     python -m backend.validation_rules.testing.synthetic_model_generator --statement-role balance_sheet
     ```
   - Full control via editable JSON spec:
     1. Generate a base JSON sidecar.
     2. Edit its `contexts`, `facts`, and `tables.rows` exactly how you want.
     3. Re-render XHTML from that spec:
     ```powershell
     python -m backend.validation_rules.testing.synthetic_model_generator --spec-file backend/validation_rules/generated/2026/frs102/synthetic_examples/ppe_note_invalid_hypercube.json --output backend/validation_rules/generated/2026/frs102/synthetic_examples/custom_ppe.html
     ```
   - That `--spec-file` path is the easiest way to hand-author synthetic reports without changing Python.

   How to run files through the harness:
   - Single synthetic or real inline XHTML file:
     ```powershell
     python -m backend.validation_rules.testing.rule_execution --input-file path\to\file.xhtml
     ```
   - Write JSON results:
     ```powershell
     python -m backend.validation_rules.testing.rule_execution --input-file path\to\file.xhtml --output path\to\results.json
     ```
   - Batch over a directory of real filings:
     ```powershell
     python -m backend.validation_rules.testing.rule_execution_batch --input-dir path\to\filings --results-dir path\to\results --recursive
     ```
   - Real files can go through the same loader as long as they are XML-well-formed inline XHTML/iXBRL.

   Next turn options:
   - add more curated negative profiles for other families
   - harden the real-file loader against messier filings
   - start separating rule severities/output for review versus enforcement

5. Questions needing taxonomy expertise:
   - No blocker for this step
   - The next taxonomy-sensitive choice is how far Family 1 should go on real filings when topic dimensions are absent: whether to allow concept-only note heuristics for loosely modelled notes, or keep note presence dimension-led for higher precision