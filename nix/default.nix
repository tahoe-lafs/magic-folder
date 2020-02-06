# This is the main entrypoint for the Tahoe-LAFS derivation.
{ pkgs ? import <nixpkgs> { } }:
# Add our Python packages to nixpkgs to simplify the expression for the
# Tahoe-LAFS derivation.
let pkgs' = pkgs.extend (import ./overlays.nix);
# Evaluate the expression for our Magic-Folder derivation.
in pkgs'.python2.pkgs.callPackage ./magic-folder.nix { }
