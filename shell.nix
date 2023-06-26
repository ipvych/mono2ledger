{ pkgs ? import <nixpkgs> {} }:
pkgs.mkShell {
  buildInputs = let
    python = pkgs.python311;
    ppkgs = ps: with ps; [ pycountry requests ];
  in [(python.withPackages ppkgs)];
}
