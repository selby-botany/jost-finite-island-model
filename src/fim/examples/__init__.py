"""The shipped worked examples and the run classes that group them.

`fim.examples.classes` reads and validates `doc/examples/classes.yaml`,
the class tree the Examples dialog shows and a configuration's `class`
label is checked against (design doc `20261005-claude-opus-5-5-read-
only-examples-and-classes-design.md`, `selby/restricted`, section 2).
`fim.examples.seed` writes the desktop app's bundled examples into the
results folder as read-only runs, Studies, and the Examples Experiment
(section 4.2). Nothing is re-exported here, so importing one submodule never pays for
another.
"""
