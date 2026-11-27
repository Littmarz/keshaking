"""Выгрузка полного корпуса регламентов."""

from retrieval.export import main as export_main


def main() -> None:
    export_main(require_complete=True)


if __name__ == "__main__":
    main()
