self: super: {
  python27 = super.python27.override (old: {
    packageOverrides = self.lib.composeExtensions
      old.packageOverrides
      (python-self: python-super: rec {
        attrs = python-self.callPackage ./attrs.nix { };
        pytest = python-self.callPackage ./pytest.nix { };
        pytest_4 = pytest;
        # The newest typing is incompatible with the packaged version of
        # Hypothesis.  Upgrading Hypothesis is like pulling on a loose thread in
        # a sweater.  I pulled it as far as pytest where I found there was no
        # upgrade route because pytest has dropped Python 2 support.
        # Fortunately, downgrading typing ends up being fairly straightforward.
        #
        # For now.  This is, no doubt, a sign of things to come for the Python 2
        # ecosystem - the early stages of a slow, painful death by the thousand
        # cuts of incompatibilities between libraries with no maintained Python
        # 2 support.
        typing = python-self.callPackage ./typing.nix { };
      });
  });
}
