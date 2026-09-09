# Deterministic PDF fixtures

Foundation 0.1 PDF fixtures are generated in memory by the
`heleos-test-fixtures` crate. No project content or third-party PDF bytes are
stored here.

Every factory fixes object numbers, trailer identifiers, metadata, dates,
producer values, and encryption inputs. The factories do not read the clock,
locale, environment, filesystem ordering, or random state. Golden tests bind
each generated vector to its SHA-256 digest.

The authoritative inventory, generation call and parameters, rights, data
classification, allowed use, owner, evaluation, and rollback instructions are
recorded in `governance/fixtures.toml`. All vectors are repository-authored
synthetic fixtures classified `PUBLIC` and admitted only for automated tests.

Do not add generated PDF files to this directory. Add or change a factory,
update its frozen hash only after review, and update the governance record in
the same change.
