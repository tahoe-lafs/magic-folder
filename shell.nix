{ pkgs }:
pkgs.mkShell {
  buildInputs = with pkgs; [
    python3
  ];
}
