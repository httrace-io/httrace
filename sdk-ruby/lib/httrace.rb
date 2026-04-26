require 'json'
require 'net/http'
require 'uri'
require 'cgi'
require 'set'
require 'thread'
require 'stringio'

module Httrace
  VERSION = '0.1.0'
  DEFAULT_ENDPOINT = 'https://api.httrace.com/v1/captures'

  SENSITIVE_URL_PARAM_RE = /api[-_]?key|apikey|token|secret|auth|password|passwd|credential|access[-_]?token/i

  # ── Outgoing call interceptor for Net::HTTP ─────────────────────────────────

  module NetHTTPInterceptor
    def request(req, body = nil, &block)
      calls = Thread.current[:httrace_outgoing]
      return super unless calls

      t0 = Process.clock_gettime(Process::CLOCK_MONOTONIC)
      response = super
      latency_ms = (Process.clock_gettime(Process::CLOCK_MONOTONIC) - t0) * 1000.0

      begin
        raw_url = "#{use_ssl? ? 'https' : 'http'}://#{address}:#{port}#{req.path}"
        clean_url = Httrace.sanitize_outgoing_url(raw_url)

        body_val = nil
        ct = response['content-type'] || ''
        if ct.include?('application/json')
          begin
            body_val = JSON.parse(response.body)
          rescue
          end
        end

        calls << {
          type:            'http',
          method:          req.method.upcase,
          url:             clean_url,
          response_status: response.code.to_i,
          response_body:   body_val,
          latency_ms:      latency_ms.round(2),
        }
      rescue
      end

      response
    end
  end

  def self.sanitize_outgoing_url(raw_url)
    parsed = URI.parse(raw_url)
    if parsed.query
      qs = URI.decode_www_form(parsed.query).map do |k, v|
        [k, SENSITIVE_URL_PARAM_RE.match?(k) ? '<REDACTED>' : v]
      end
      parsed.query = URI.encode_www_form(qs)
    end
    parsed.to_s
  rescue
    raw_url
  end

  SENSITIVE_HEADERS = Set.new(%w[
    authorization cookie set-cookie x-api-key x-auth-token proxy-authorization
  ])

  BINARY_CONTENT_TYPES = %w[
    application/octet-stream image/ audio/ video/
    application/gzip application/zip application/pdf multipart/
  ]

  SENSITIVE_KEYS = %w[password secret token ssn credit_card card_number cvv]

  # ── CaptureMiddleware ──────────────────────────────────────────────────────
  #
  # Rack middleware — works with Rails, Sinatra, Grape, and any Rack-compatible app.
  #
  # Rails (config/application.rb):
  #   config.middleware.use Httrace::CaptureMiddleware, api_key: 'ht_...'
  #
  # Sinatra:
  #   use Httrace::CaptureMiddleware, api_key: 'ht_...'
  #
  class CaptureMiddleware
    # @param app             [#call]  the Rack app to wrap
    # @param api_key         [String] your Httrace API key
    # @param service         [String] service label shown in the dashboard
    # @param sample_rate     [Float]  fraction of requests to capture (0.0–1.0)
    # @param exclude_paths   [Array]  paths to skip
    # @param endpoint        [String] override API endpoint (for self-hosted)
    # @param capture_outgoing[Boolean] capture outgoing Net::HTTP + ActiveRecord SQL calls
    def initialize(app, api_key:, service: 'default', sample_rate: 0.1,
                   exclude_paths: nil, endpoint: nil, capture_outgoing: false)
      @app              = app
      @service          = service
      @sample_rate      = sample_rate.to_f
      @exclude          = Set.new(exclude_paths || %w[/health /metrics /favicon.ico])
      @client           = Client.new(api_key, endpoint || DEFAULT_ENDPOINT)
      @capture_outgoing = capture_outgoing

      if capture_outgoing
        Net::HTTP.prepend(Httrace::NetHTTPInterceptor)
      end
    end

    def call(env)
      path = env['PATH_INFO'] || '/'

      if @exclude.include?(path) || rand >= @sample_rate
        return @app.call(env)
      end

      # Buffer request body so we can read it AND so the app can read it too
      raw_input = env['rack.input'] || StringIO.new
      req_body  = raw_input.read
      env['rack.input'] = StringIO.new(req_body)

      # Activate outgoing call capture for this request thread
      if @capture_outgoing
        Thread.current[:httrace_outgoing] = []
      end

      t_start = Process.clock_gettime(Process::CLOCK_MONOTONIC)
      status, headers, body_iter = @app.call(env)
      latency_ms = (Process.clock_gettime(Process::CLOCK_MONOTONIC) - t_start) * 1000.0

      # Collect outgoing calls before clearing the thread-local
      outgoing_calls = @capture_outgoing ? (Thread.current[:httrace_outgoing] || []).dup : []
      Thread.current[:httrace_outgoing] = nil if @capture_outgoing

      # Collect response body without consuming it (Rack contract: body is enumerable)
      resp_parts = []
      body_iter.each { |chunk| resp_parts << chunk }
      resp_body_str = resp_parts.join

      Thread.new do
        record(env, req_body, status, headers, resp_body_str, latency_ms, outgoing_calls)
      rescue
        # Never crash
      end

      [status, headers, [resp_body_str]]
    end

    private

    def record(env, req_body, status, resp_headers, resp_body, latency_ms, outgoing_calls = [])
      req_ct   = env['CONTENT_TYPE'] || ''
      resp_ct  = resp_headers['Content-Type'] || resp_headers['content-type'] || ''

      query_params = parse_query(env['QUERY_STRING'])

      interaction = {
        service:        @service,
        session_id:     env['HTTP_X_SESSION_ID'] || env['HTTP_X_REQUEST_ID'],
        outgoing_calls: outgoing_calls,
        request: {
          method:       env['REQUEST_METHOD'],
          path:         env['PATH_INFO'],
          query_params: query_params,
          headers:      filter_headers(env),
          body:         sanitize(parse_body(req_body, req_ct)),
          timestamp:    Time.now.to_f,
        },
        response: {
          status_code: status.to_i,
          headers:     {},
          body:        sanitize(parse_body(resp_body, resp_ct)),
          latency_ms:  latency_ms.round(2),
        },
      }

      @client.enqueue(interaction)
    end

    def filter_headers(env)
      env.each_with_object({}) do |(key, val), h|
        next unless key.start_with?('HTTP_')
        name = key[5..].downcase.tr('_', '-')
        h[name] = val unless SENSITIVE_HEADERS.include?(name)
      end
    end

    def parse_body(raw, content_type)
      return nil if raw.nil? || raw.empty?
      return nil if BINARY_CONTENT_TYPES.any? { |t| content_type.include?(t) }

      if content_type.include?('application/json')
        begin
          return JSON.parse(raw)
        rescue JSON::ParserError
          # fall through
        end
      end

      raw.encode('UTF-8', invalid: :replace, undef: :replace)
    end

    def parse_query(qs)
      return {} if qs.nil? || qs.empty?
      qs.split('&').each_with_object({}) do |pair, h|
        k, v = pair.split('=', 2)
        h[CGI.unescape(k.to_s)] = CGI.unescape(v.to_s) if k
      end
    end

    def sanitize(val)
      case val
      when Hash
        val.each_with_object({}) do |(k, v), h|
          lk = k.to_s.downcase
          h[k] = SENSITIVE_KEYS.any? { |s| lk.include?(s) } ? '[REDACTED]' : sanitize(v)
        end
      when Array
        val.map { |v| sanitize(v) }
      when String
        val
          .gsub(/\b[\w.+\-]+@[\w.\-]+\.\w{2,}\b/, '[EMAIL]')
          .gsub(/\b(?:\d[ \-]?){13,16}\b/, '[CARD]')
      else
        val
      end
    end
  end

  # ── Client ─────────────────────────────────────────────────────────────────

  class Client
    BATCH_SIZE = 50
    MAX_QUEUE  = 10_000

    def initialize(api_key, endpoint)
      @api_key  = api_key
      @uri      = URI.parse(endpoint)
      @queue    = []
      @mu       = Mutex.new
      @worker   = Thread.new { run_worker }
      @worker.abort_on_exception = false
    end

    def enqueue(interaction)
      @mu.synchronize do
        @queue << interaction if @queue.size < MAX_QUEUE
      end
    end

    private

    def run_worker
      loop do
        sleep 2
        flush
      end
    rescue
      # ignore
    end

    def flush
      batch = @mu.synchronize do
        return if @queue.empty?
        b = @queue.dup
        @queue.clear
        b
      end

      return if batch.nil? || batch.empty?

      http = Net::HTTP.new(@uri.host, @uri.port)
      http.use_ssl     = @uri.scheme == 'https'
      http.open_timeout = 3
      http.read_timeout = 5

      req = Net::HTTP::Post.new(@uri.path)
      req['Content-Type'] = 'application/json'
      req['X-Api-Key']    = @api_key
      req.body            = JSON.generate(captures: batch)

      http.request(req)
    rescue
      # fire-and-forget
    end
  end
end
