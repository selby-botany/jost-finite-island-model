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
  end

  test do
    assert_match(/^fim \d+\.\d+\.\d+$/, shell_output("#{bin}/fim --version").strip)
    assert_match "run", shell_output("#{bin}/fim --help")
  end
end
