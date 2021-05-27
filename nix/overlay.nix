self: super: {
  python2 = super.python2.override {
    packageOverrides = python-self: python-super: {
      klein = python-self.callPackage ./klein.nix { };
    };
  };
}
