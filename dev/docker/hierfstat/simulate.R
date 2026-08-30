#!/usr/bin/env Rscript
# Run hierfstat's own symmetric-island simulation and print the raw,
# whole-population genotype data frame as CSV -- deliberately never
# hierfstat's own summary statistics (`basic.stats`/`wc`/`betas`), which
# are finite-sample bias-corrected estimators, not the exact quantity
# this project's own `fim.statistics` computes. See `1121-citrus`'s
# `20260830-claude-sonnet-5-external-tooling-cross-validation-plan.md`
# for the full reasoning.
#
# Usage:
#   Rscript simulate.R <N_diploid> <m> <mu> <d> <generations> <nbal> \
#       <nbloc> <seed>
#
# All arguments are required and positional, matched by
# `dev/bin/compare-against-hierfstat`'s own invocation -- this script is
# not meant to be run by hand with defaults guessed at.

suppressPackageStartupMessages(library(hierfstat))

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 8) {
    stop("usage: simulate.R N_diploid m mu d generations nbal nbloc seed")
}

n_diploid <- as.integer(args[1])
m <- as.numeric(args[2])
mu <- as.numeric(args[3])
d <- as.integer(args[4])
generations <- as.integer(args[5])
nbal <- as.integer(args[6])
nbloc <- as.integer(args[7])
seed <- as.integer(args[8])

set.seed(seed)

# Symmetric island migration matrix: each deme keeps `1 - m` of its own
# frequency and sends the rest split evenly among the other `d - 1`
# demes -- the same convention `fim.model.topology`'s own dense-matrix
# builders already use, so a matched scenario's matrix is identical in
# shape and meaning on both sides of the comparison.
migration <- matrix(m / (d - 1), nrow = d, ncol = d)
diag(migration) <- 1 - m

# `size = N`: request the *entire* simulated population, not a sample
# of it -- this is what avoids hierfstat's own finite-sample estimator
# machinery downstream. The returned data frame is this run's exact,
# complete ground truth, the same sense in which this project's own
# `ModelState` is exact.
genotypes <- sim.genot.metapop.t(
    size = n_diploid,
    nbal = nbal,
    nbloc = nbloc,
    nbpop = d,
    N = n_diploid,
    mig = migration,
    mut = mu,
    f = 0,
    t = generations
)

write.csv(genotypes, file = stdout(), row.names = FALSE)
