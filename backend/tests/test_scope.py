def test_api_restart_does_not_interrupt_separate_cli_evaluation(components):
    _, repo, _, _, _, _ = components
    cli = repo.create_run(repo.create_session()['id'], '独立CLI验收', execution_scope='cli')
    api = repo.create_run(repo.create_session()['id'], 'API运行')
    repo.recover_interrupted()
    assert repo.run(cli['id'])['status'] == 'created'
    assert repo.run(api['id'])['status'] == 'failed'
