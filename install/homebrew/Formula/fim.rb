class Fim < Formula
  desc "Finite island model simulator with per-generation trajectories"
  homepage "https://github.com/selby-botany/jost-finite-island-model"
  license "AGPL-3.0-or-later"
  head "https://github.com/selby-botany/jost-finite-island-model.git", branch: "dev"

  depends_on "python@3.12"

  def install
    python = Formula["python@3.12"].opt_bin/"python3.12"
    system python, "-m", "venv", libexec
    system libexec/"bin/python", "-m", "pip", "install", "."
    bin.install_symlink libexec/"bin/fim"
    bin.install_symlink libexec/"bin/fim-gui"
  end

  test do
    assert_match(/^fim \d+\.\d+\.\d+$/, shell_output("#{bin}/fim --version").strip)
    assert_match "run", shell_output("#{bin}/fim --help")
    # fim-gui launches a real Tk root, which brew test's headless CI
    # environment cannot render -- confirm the entry point exists and is
    # executable rather than actually starting it (mirrors why this
    # formula's own CI validation, install/homebrew/test-formula, checks
    # `brew install` succeeds rather than driving the GUI).
    assert_predicate bin/"fim-gui", :executable?
  end
end
