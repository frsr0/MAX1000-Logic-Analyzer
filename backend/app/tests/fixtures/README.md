# Protocol fixture format

Fixtures contain a compact `digital` bit-mask array, channel-role mappings,
decoder settings, and expected event objects. They are intentionally independent
of hardware and can be generated from a protocol encoder or hand-authored for
malformed-input cases. The schema is in `protocol_fixture.schema.json`.

Fixture consumers validate the schema and compare complete expected events;
malformed-input fixtures are expected to state the precise error behavior.
They are part of the 541-test backend gate, which currently has 100% statement
and branch coverage.
