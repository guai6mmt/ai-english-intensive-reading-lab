import uuid

import app as application


def imported(client, csrf):
    text = 'A quieter way to learn\n\nDr. Smith reads every morning. He listens to the original recording. '
    text += 'This sentence contains enough meaningful English words to make a useful reading exercise. ' * 5
    response = client.post('/api/upload', headers={'X-CSRF-Token': csrf}, files={'file': (uuid.uuid4().hex + '.txt', text.encode(), 'text/plain')})
    assert response.status_code == 200, response.text
    return response.json()['source']['articles'][0]['id']


def test_translation_save_review_and_cache(authenticated_client, monkeypatch):
    client, csrf = authenticated_client
    aid = imported(client, csrf)
    calls = []
    def fake(*args, **kwargs):
        calls.append(args)
        return {'translation': '他听原版录音。'}, {'used_ai': True, 'provider': 'deepseek', 'model': 'test'}
    monkeypatch.setattr(application, 'call_ai_json', fake)
    headers = {'X-CSRF-Token': csrf}
    spec = {'article_id': aid, 'sentence': 'He listens to the original recording.'}
    first = client.post('/api/sentences/translate', json=spec, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()['cached'] is False
    assert client.post('/api/sentences/translate', json=spec, headers=headers).json()['cached'] is True
    saved = client.post('/api/sentences', json=spec, headers=headers).json()['item']
    again = client.post('/api/sentences', json=spec, headers=headers).json()['item']
    assert saved['id'] == again['id']
    assert len(calls) == 1
    assert saved['kind'] == 'sentence'
    assert saved['context_items'][0]['article_id'] == aid
    assert saved['term'] == spec['sentence']
    reviewed = client.post('/api/vocabulary/review/' + saved['id'], json={'rating': 'good'}, headers=headers)
    assert reviewed.status_code == 200, reviewed.text
    assert all(i['kind'] == 'sentence' for i in client.get('/api/vocabulary?kind=sentence').json()['items'])
    assert client.post('/api/sentences/translate', json={**spec, 'sentence': 'Not in this article.'}, headers=headers).status_code == 400
    assert client.post('/api/sentences/translate', json=spec).status_code == 403


def test_failed_ai_is_not_a_translation(authenticated_client, monkeypatch):
    client, csrf = authenticated_client
    aid = imported(client, csrf)
    monkeypatch.setattr(application, 'call_ai_json', lambda *a, **k: (None, {'used_ai': False}))
    response = client.post('/api/sentences/translate', json={'article_id': aid, 'sentence': 'Dr. Smith reads every morning.'}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 503


def test_cleaned_reader_sentence_is_accepted(monkeypatch):
    from english_lab.sentences import SentenceSelection, selection
    monkeypatch.setattr(application, 'find_article', lambda _article_id: {'id': 'article-1'})
    monkeypatch.setattr(application, 'article_text', lambda _article, cleaned=True: 'A cleaned sentence is shown here.' if cleaned else 'A  cleaned sentence is shown here .')
    _article, sentence, context = selection(SentenceSelection(article_id='article-1', sentence='A cleaned sentence is shown here.'))
    assert sentence in context


def test_retired_analysis_routes_are_removed():
    routes = {getattr(r, 'path', '') for r in application.app.routes}
    for suffix in ('pack', 'text-check', 'overview', 'paragraphs/analyze', 'long-sentences', 'vocabulary/analyze', 'reading/questions', 'sentence/analyze', 'listening/prepare'):
        assert '/api/articles/{article_id}/' + suffix not in routes
