import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))

import install_repo_skills


class InstallRepoSkillsTests(unittest.TestCase):
    def test_resolve_selected_names_uses_manifest_names(self):
        manifest = {
            'skills': [
                {'name': 'ecp-timereport-autofill', 'path': 'skills/ecp-timereport-autofill'},
                {'name': 'demo-skill', 'path': 'skills/demo-skill'},
            ]
        }

        result = install_repo_skills.resolve_selected_names(
            manifest,
            ['demo-skill', 'ecp-timereport-autofill'],
            install_all=False,
        )

        self.assertEqual(result, ['demo-skill', 'ecp-timereport-autofill'])

    def test_build_source_uses_github_url_for_non_default_ref(self):
        self.assertEqual(
            install_repo_skills.build_source('qiuyusong/work-skill-tools', 'feature/test'),
            'https://github.com/qiuyusong/work-skill-tools.git#feature/test',
        )

    def test_build_install_command_uses_npx_skills_add(self):
        command = install_repo_skills.build_install_command(
            repo='qiuyusong/work-skill-tools',
            ref='main',
            selected_names=['ecp-timereport-autofill'],
            install_all=False,
            agents=['codex'],
            global_install=True,
            assume_yes=True,
            copy_files=False,
            full_depth=False,
        )

        self.assertEqual(
            command,
            [
                'npx',
                'skills',
                'add',
                'qiuyusong/work-skill-tools',
                '--skill',
                'ecp-timereport-autofill',
                '--agent',
                'codex',
                '--global',
                '--yes',
            ],
        )


if __name__ == '__main__':
    unittest.main()
