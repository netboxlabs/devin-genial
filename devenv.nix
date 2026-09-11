{ pkgs, ... }: {
  packages = [ pkgs.python3 pkgs.just ];
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
