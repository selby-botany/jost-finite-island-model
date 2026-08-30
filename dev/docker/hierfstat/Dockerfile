# Independent reference implementation for `dev/bin/compare-against-
# hierfstat`. Builds a minimal R environment with hierfstat (Goudet's
# group, CRAN) installed, used purely as an independently-written
# simulator of the same discrete migration-plus-drift(-plus-mutation)
# process this project's own `fim()` engine implements -- see
# `1121-citrus`'s `20260830-claude-sonnet-5-external-tooling-cross-
# validation-plan.md` design doc for why, and why hierfstat's own
# summary-statistic functions (`basic.stats`/`wc`/`betas`) are
# deliberately never called; only its raw simulated genotypes are used.
#
# Not built or run by `build`/`ci.yml` -- a manually-invoked comparison
# tool, the same "deliberately outside the deterministic PR gate"
# category `dev/bin/calibrate-statistical-bands` already established.
FROM r-base:4.6.1

# hierfstat's own CRAN dependency chain (ade4, adegenet, gaston,
# gtools, methods; DESCRIPTION checked directly against the package's
# GitHub repository this session). `gaston` compiles C++ against
# BLAS/LAPACK/zlib; the rest are pure R or thin C wrappers -- all
# installed from CRAN source, not a curated binary repository, since
# this image is built by hand, occasionally, not in CI.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        cmake \
        gfortran \
        libblas-dev \
        liblapack-dev \
        libuv1-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Pinned to the CRAN version confirmed against the package's own
# DESCRIPTION file this session (2022-05-03) -- a version drift here
# would silently change what "hierfstat says" means between runs of
# this tool, the same reasoning behind pinning every other external
# tool this project depends on. The final `library(hierfstat)` line is
# a hard build-time check, not decoration: `install.packages`/`remotes::
# install_version` do not themselves fail the build on a broken
# dependency chain (confirmed the hard way this session -- an earlier
# version of this Dockerfile "succeeded" while silently leaving
# hierfstat uninstalled); this line turns that into an immediate,
# loud build failure instead.
RUN R -e 'install.packages("remotes", repos="https://cloud.r-project.org")' \
    && R -e 'remotes::install_version("hierfstat", version="0.5-11", repos="https://cloud.r-project.org")' \
    && R -e 'library(hierfstat)'

WORKDIR /work
COPY simulate.R /work/simulate.R

ENTRYPOINT ["Rscript", "/work/simulate.R"]
