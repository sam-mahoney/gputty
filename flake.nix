{
  description = "gputty - minimal WebGPU tty fed by a Python log stream";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-darwin" "x86_64-darwin" "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAll (pkgs: {
        default = pkgs.mkShell { packages = [ pkgs.python3 ]; };
      });

      apps = forAll (pkgs: {
        default = {
          type = "app";
          program = "${pkgs.writeShellScript "gputty" ''
            exec ${pkgs.python3}/bin/python3 ${self}/server.py "$@"
          ''}";
        };
      });
    };
}
