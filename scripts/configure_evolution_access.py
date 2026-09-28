"""Configure direct Evolution Manager access without changing integration secrets."""

import argparse
import os
import re
import tempfile
from ipaddress import IPv4Address
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure_access(env_file: Path, host: str, port: int) -> str:
    address = IPv4Address(host)
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValueError("Informe o IP da VPS, nao um endereco local ou multicast.")
    if not 1024 <= port <= 65535:
        raise ValueError("A porta deve estar entre 1024 e 65535.")
    if not env_file.is_file():
        raise ValueError("O .env nao existe. Copie seu arquivo para a pasta do projeto primeiro.")

    public_url = f"http://{address}:{port}"
    updates = {
        "EVOLUTION_BIND_ADDRESS": "0.0.0.0",
        "EVOLUTION_PORT": str(port),
        "EVOLUTION_SERVER_URL": public_url,
    }
    original = env_file.read_bytes().decode("utf-8-sig")
    content = original
    newline = "\r\n" if "\r\n" in original else "\n"
    for name, value in updates.items():
        pattern = rf"(?m)^[ \t]*(?:export[ \t]+)?{name}[ \t]*=[^\r\n]*"
        if len(re.findall(pattern, content)) > 1:
            raise ValueError(f"Variavel repetida no .env: {name}. Remova a duplicata primeiro.")
        replacement = f"{name}={value}"
        if re.search(pattern, content):
            content = re.sub(pattern, replacement, content)
        else:
            if content and not content.endswith(("\n", "\r")):
                content += newline
            content += replacement + newline

    # Atomic replacement; never create a backup containing copies of credentials.
    # The temporary file stays beside .env so the replacement uses the same filesystem.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=env_file.parent, prefix=".env.", delete=False) as file:
            temporary = Path(file.name)
            os.chmod(temporary, 0o600)
            file.write(content.encode("utf-8"))
        os.replace(temporary, env_file)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return public_url


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configura o painel Evolution no IP da VPS, sem tunel SSH."
    )
    parser.add_argument("--host", required=True, help="IP IPv4 da VPS")
    parser.add_argument("--port", type=int, default=8090, help="Porta externa (padrao: 8090)")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    try:
        public_url = configure_access(args.env_file.resolve(), args.host, args.port)
    except (ValueError, OSError, UnicodeError) as error:
        parser.exit(1, f"Erro: {error}\n")
    print(f"Painel: {public_url}/manager")
    print(f"URL da API no login: {public_url}")
    print("API Key: use EVOLUTION_API_KEY do seu .env; nenhuma chave foi alterada.")
    print("Webhook interno: http://api:8000/webhooks/evolution")
    print("Aplique: docker compose up -d --force-recreate evolution")


if __name__ == "__main__":
    main()
