require 'minitest/autorun'
require 'stringio'
require_relative '../lib/httrace'

class FakeApp
  def initialize(status: 200, body: '{"ok":true}', headers: { 'Content-Type' => 'application/json' })
    @status  = status
    @body    = body
    @headers = headers
  end

  def call(_env)
    [@status, @headers, [@body]]
  end
end

class TestHttraceMiddleware < Minitest::Test
  def build_env(path: '/api/orders', method: 'GET', body: '', content_type: 'application/json', extra: {})
    {
      'REQUEST_METHOD' => method,
      'PATH_INFO'      => path,
      'QUERY_STRING'   => '',
      'CONTENT_TYPE'   => content_type,
      'rack.input'     => StringIO.new(body),
    }.merge(extra)
  end

  def test_passes_through_response_unchanged
    mw = Httrace::CaptureMiddleware.new(FakeApp.new, api_key: 'ht_test', sample_rate: 1.0)
    status, _headers, body = mw.call(build_env)
    assert_equal 200, status
    assert_equal '{"ok":true}', body.first
  end

  def test_excluded_path_not_modified
    mw = Httrace::CaptureMiddleware.new(FakeApp.new(status: 200, body: 'ok'), api_key: 'ht_test', sample_rate: 1.0)
    status, _headers, body = mw.call(build_env(path: '/health'))
    assert_equal 200, status
    assert_equal 'ok', body.first
  end

  def test_sample_rate_zero_never_captures
    calls = 0
    fake = Class.new do
      define_method(:call) { |env| calls += 1; [200, {}, ['ok']] }
    end.new
    mw = Httrace::CaptureMiddleware.new(fake, api_key: 'ht_test', sample_rate: 0.0)
    10.times { mw.call(build_env) }
    assert_equal 10, calls
  end

  def test_request_body_still_readable_by_app
    read_body = nil
    fake_app = lambda do |env|
      read_body = env['rack.input'].read
      [200, {}, ['ok']]
    end
    mw = Httrace::CaptureMiddleware.new(fake_app, api_key: 'ht_test', sample_rate: 1.0)
    payload = '{"cart_id":"abc123"}'
    mw.call(build_env(method: 'POST', body: payload))
    assert_equal payload, read_body, 'App could not read the request body'
  end

  def test_filter_headers_removes_sensitive
    mw = Httrace::CaptureMiddleware.new(FakeApp.new, api_key: 'ht_test', sample_rate: 1.0)
    env = {
      'HTTP_AUTHORIZATION' => 'Bearer secret',
      'HTTP_COOKIE'        => 'session=abc',
      'HTTP_X_CUSTOM'      => 'kept',
    }
    filtered = mw.send(:filter_headers, env)
    refute_includes filtered, 'authorization', 'authorization should be filtered'
    refute_includes filtered, 'cookie', 'cookie should be filtered'
    assert_equal 'kept', filtered['x-custom']
  end

  def test_parse_body_json
    mw = Httrace::CaptureMiddleware.new(FakeApp.new, api_key: 'ht_test', sample_rate: 1.0)
    result = mw.send(:parse_body, '{"user_id":42}', 'application/json')
    assert_equal 42, result['user_id']
  end

  def test_parse_body_binary_returns_nil
    mw = Httrace::CaptureMiddleware.new(FakeApp.new, api_key: 'ht_test', sample_rate: 1.0)
    result = mw.send(:parse_body, "\x89PNG\r\n", 'image/png')
    assert_nil result
  end

  def test_sanitize_redacts_password
    mw = Httrace::CaptureMiddleware.new(FakeApp.new, api_key: 'ht_test', sample_rate: 1.0)
    input  = { 'username' => 'alice', 'password' => 'hunter2' }
    output = mw.send(:sanitize, input)
    assert_equal '[REDACTED]', output['password']
    assert_equal 'alice',      output['username']
  end

  def test_sanitize_redacts_email_in_string
    mw = Httrace::CaptureMiddleware.new(FakeApp.new, api_key: 'ht_test', sample_rate: 1.0)
    result = mw.send(:sanitize, 'Contact user@example.com for info')
    assert_includes result, '[EMAIL]'
    refute_includes result, 'user@example.com'
  end

  def test_client_enqueue
    client = Httrace::Client.new('ht_test', 'http://localhost:19999')
    # Just verify it doesn't raise
    client.enqueue({ service: 'test', request: {}, response: {} })
    assert true
  end
end
