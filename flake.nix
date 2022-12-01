{
  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
      };
    in {

      packages.${system}.default = pkgs.python3.pkgs.buildPythonPackage {
        pname = "magic-folder";
        version = "22.2.2";
        src = ./.;

        buildInputs = with pkgs.python3.pkgs; [
          zope_interface
          humanize
          eliot
          autobahn
          hyperlink
          (pkgs.python3.pkgs.toPythonModule pkgs.tahoe-lafs)
          treq
          appdirs
          pyutil
          cryptography
          klein
          psutil
          filelock
        ];

        doCheck = false;
        doInstallCheck = false;

      };

      devShells.x86_64-linux.default = import ./shell.nix { inherit pkgs; };
    };
}
