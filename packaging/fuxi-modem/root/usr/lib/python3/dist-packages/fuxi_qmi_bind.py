def check_response_header(qmi, request, response):
    fields = (qmi.message_get_service, qmi.message_get_client_id,
              qmi.message_get_transaction_id, qmi.message_get_message_id)
    if not qmi.message_is_response(response) or any(field(response) != field(request) for field in fields):
        raise RuntimeError("Unexpected subscription response header")


def bind_subscription(qmi, wait, device, client, message_id, subscription):
    request = qmi.message_new(client.get_service(), client.get_cid(),
                              client.get_next_transaction_id(), message_id)
    offset = qmi.message_tlv_write_init(request, 0x01)
    qmi.message_tlv_write_guint8(request, subscription)
    qmi.message_tlv_write_complete(request, offset)
    _, result = wait(device.command_full, request, qmi.MessageContext.new(), 10, None)
    response = device.command_full_finish(result)
    check_response_header(qmi, request, response)
    offset, length = qmi.message_tlv_read_init(response, 0x02)
    if length != 4:
        raise RuntimeError("Invalid subscription bind result length")
    valid, cursor, status = qmi.message_tlv_read_guint16(response, offset, 0, qmi.Endian.LITTLE)
    if not valid:
        raise RuntimeError("Invalid subscription bind status")
    valid, _, error = qmi.message_tlv_read_guint16(response, offset, cursor, qmi.Endian.LITTLE)
    if not valid or status or error:
        raise RuntimeError(f"Subscription bind failed (status={status}, error={error})")
