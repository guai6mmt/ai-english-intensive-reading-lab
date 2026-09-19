import uuid
import time
import threading

import app as application


def imported(client, csrf):
    text = 'A quieter way to learn ' + uuid.uuid4().hex + '\n\nDr. Smith reads every morning. He listens to the original recording. '
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
        return {
            'translation': '他听原版录音。',
            'structure': '主语 He + 谓语 listens + 介词短语。',
            'clauses': [{
                'text': 'He listens', 'role': '主句主干',
                'explanation': 'He 是主语，listens 是谓语。',
            }, {
                'text': 'to the original recording', 'role': '介词短语',
                'explanation': '说明倾听的对象。',
            }],
            'vocabulary': [{
                'term': 'original recording', 'pos': '名词短语',
                'meaning': '原版录音', 'usage': 'listen to the original recording',
            }],
        }, {'used_ai': True, 'provider': 'deepseek', 'model': 'test'}
    monkeypatch.setattr(application, 'call_ai_json', fake)
    headers = {'X-CSRF-Token': csrf}
    spec = {'article_id': aid, 'sentence': 'He listens to the original recording.'}
    first = client.post('/api/sentences/translate', json=spec, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()['cached'] is False
    assert first.json()['structure'].startswith('主语 He')
    assert first.json()['clauses'][1]['role'] == '介词短语'
    assert first.json()['vocabulary'][0]['term'] == 'original recording'
    assert 'grammar_points' not in first.json()
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


def test_incomplete_ai_analysis_is_rejected(authenticated_client, monkeypatch):
    client, csrf = authenticated_client
    aid = imported(client, csrf)
    monkeypatch.setattr(application, 'call_ai_json', lambda *a, **k: (
        {'translation': '只有译文。'},
        {'used_ai': True, 'provider': 'deepseek', 'model': 'test'},
    ))
    response = client.post('/api/sentences/translate', json={
        'article_id': aid, 'sentence': 'Dr. Smith reads every morning.',
    }, headers={'X-CSRF-Token': csrf})
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


def wait_batch(client, aid):
    for _ in range(150):
        status = client.get('/api/sentences/batch/' + aid).json()
        if not status['busy']:
            return status
        time.sleep(.02)
    raise AssertionError('batch worker did not finish')


def test_batch_order_failure_retry_and_cache(authenticated_client, monkeypatch):
    import json
    client, csrf = authenticated_client
    aid = imported(client, csrf)
    headers = {'X-CSRF-Token': csrf}
    sentences = ['Dr. Smith reads every morning.', 'He listens to the original recording.']
    calls = []
    fail = [True]
    def fake(provider, prompt, content, **kwargs):
        sentence = json.loads(content)['sentence']
        calls.append(sentence)
        if sentence == sentences[1] and fail[0]:
            return None, {'used_ai': False}
        return {'translation': '译文', 'structure': '主干', 'clauses': [
            {'text': sentence, 'role': '主句', 'explanation': '说明'}], 'vocabulary': []}, {
            'used_ai': True, 'provider': 'deepseek', 'model': 'test'}
    monkeypatch.setattr(application, 'call_ai_json', fake)
    payload = {'article_id': aid, 'sentences': sentences}
    assert client.post('/api/sentences/batch', json=payload).status_code == 403
    assert client.post('/api/sentences/batch', json={**payload, 'sentences': ['   ']}, headers=headers).status_code == 400
    assert client.post('/api/sentences/batch', json={**payload, 'sentences': ['Invented sentence.']}, headers=headers).status_code == 400
    assert client.post('/api/sentences/batch', json=payload, headers=headers).status_code == 200
    result = wait_batch(client, aid)
    assert result['state'] == 'partial' and result['completed'] == 1 and result['failed'] == 1
    assert calls == sentences
    fail[0] = False
    client.post('/api/sentences/batch', json=payload, headers=headers)
    result = wait_batch(client, aid)
    assert result['state'] == 'completed' and result['completed'] == 2
    assert calls == [*sentences, sentences[1]]
    assert client.post('/api/sentences/translate', json={'article_id': aid, 'sentence': sentences[1]}, headers=headers).json()['cached']


def test_batch_pause_and_resume(authenticated_client, monkeypatch):
    from english_lab import sentences as module
    client, csrf = authenticated_client
    aid = imported(client, csrf)
    headers = {'X-CSRF-Token': csrf}
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow(spec):
        calls.append(spec.sentence)
        entered.set()
        assert release.wait(3)
        return {}
    monkeypatch.setattr(module, 'analyze_sentence', slow)
    payload = {'article_id': aid, 'sentences': ['Dr. Smith reads every morning.', 'He listens to the original recording.']}
    client.post('/api/sentences/batch', json=payload, headers=headers)
    try:
        assert entered.wait(2)
        client.post('/api/sentences/batch', json=payload, headers=headers)
        assert len(calls) == 1
        result = client.post('/api/sentences/batch/' + aid + '/pause', json={}, headers=headers).json()
        assert result['state'] == 'paused'
    finally:
        release.set()
    result = wait_batch(client, aid)
    assert result['completed'] == 1 and result['state'] == 'paused'
    client.post('/api/sentences/batch', json=payload, headers=headers)
    assert wait_batch(client, aid)['completed'] == 2


def test_vocabulary_validation():
    from english_lab.sentences import _normalize_analysis
    value = {'translation': '译文', 'structure': '主干', 'clauses': [
        {'text': 'Hello.', 'role': '问候', 'explanation': '问候语'}], 'vocabulary': []}
    assert _normalize_analysis(value)['vocabulary'] == []
    assert _normalize_analysis({**value, 'vocabulary': [{}]}) is None


def test_concurrent_analysis_uses_one_ai_request(authenticated_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from english_lab.sentences import analyze_sentence, SentenceSelection
    client, csrf = authenticated_client
    aid = imported(client, csrf)
    calls = []
    def fake(*args, **kwargs):
        calls.append(1)
        time.sleep(.05)
        return {'translation': '译文', 'structure': '主干', 'clauses': [
            {'text': 'He listens', 'role': '主句', 'explanation': '说明'}], 'vocabulary': []}, {
            'used_ai': True, 'provider': 'deepseek', 'model': 'test'}
    monkeypatch.setattr(application, 'call_ai_json', fake)
    spec = SentenceSelection(article_id=aid, sentence='He listens to the original recording.')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: analyze_sentence(spec), range(2)))
    assert len(calls) == 1
    assert sorted(r['cached'] for r in results) == [False, True]
