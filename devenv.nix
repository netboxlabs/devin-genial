{ pkgs, ... }: {
  # pyarrow serves only the loader's optional Parquet upload path; generation,
  # validation and export stay Python-standard-library.
  packages = [ (pkgs.python3.withPackages (ps: [ ps.pyarrow ])) pkgs.just ];
  profiles.diode.module = {
    languages.python = {
      enable = true;
      venv.enable = true;
      venv.requirements = ''netboxlabs-diode-sdk==1.14.0'';
    };
  };
  enterShell = ''echo "devin-generator: just generate / just check"'';
  enterTest = ''just check'';
}
